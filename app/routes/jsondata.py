"""
MR AI RAG - JSON / API Data Ingestion Route v2
Endpoints:
  POST /ingest-json-url    → Fetch JSON from URL, index for RAG
  POST /ingest-json-file   → Upload .json file, index for RAG
  POST /preview-json-url   → Preview without indexing
  GET  /json-records       → Raw records for data viewer
"""

import json, logging, re, uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from app.core.api_keys import require_api_key
from pydantic import BaseModel

from app.models.schemas import ChunkMetadata
from app.services.embedder import embed_texts
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_RECORDS   = 5000
BATCH_SIZE    = 50
CHUNK_RECORDS = 5

_raw_records: Dict[str, List[Dict]] = {}
_raw_meta: Dict[str, Dict] = {}


class JsonUrlRequest(BaseModel):
    url: str
    data_key: str = "data"
    label: str    = ""
    refresh: bool = False

class PreviewRequest(BaseModel):
    url: str
    data_key: str = "data"

class JsonIngestResponse(BaseModel):
    success: bool
    source_id: str
    label: str
    url: str
    total_records: int
    total_chunks: int
    fields_detected: List[str]
    sample_record: Dict
    message: str

class PreviewResponse(BaseModel):
    success: bool
    total_records: int
    fields_detected: List[str]
    sample_record: Dict
    data_summary: Dict
    suggested_questions: List[str]

class RecordsResponse(BaseModel):
    source_id: str
    label: str
    total: int
    fields: List[str]
    records: List[Dict]


def _value_str(v: Any) -> str:
    if v is None: return "unknown"
    if isinstance(v, list): return ", ".join(str(x) for x in v) if v else "none"
    if isinstance(v, float): return f"{v:.4f}"
    return str(v).strip()

def _record_to_text(record: Dict, index: int) -> str:
    parts = [f"Record #{index + 1}"]
    plate  = record.get("plate_number") or record.get("plate") or ""
    vtype  = record.get("vehicle_type") or record.get("type") or ""
    vid    = record.get("vehicle_id") or ""
    color  = record.get("color") or "unknown"
    status = record.get("status") or ""
    viols  = record.get("violations") or []
    ts     = record.get("timestamp") or ""
    frame  = record.get("frame_number") or ""
    conf   = record.get("confidence")
    job    = record.get("job_id") or ""
    rid    = record.get("id") or record.get("_id") or ""

    if plate:
        parts.append(f"Plate number: {plate}")
        parts.append(f"License plate: {plate}")
    if vtype:
        parts.append(f"Vehicle type: {vtype}")
        parts.append(f"Vehicle is a {vtype}")
    if vid:    parts.append(f"Vehicle ID: {vid}")
    if color and color.lower() != "unknown":
        parts.append(f"Color: {color}")
    if status: parts.append(f"Status: {status}")
    if viols:
        v_str = ", ".join(str(v) for v in viols)
        parts.append(f"Violations: {v_str}")
        parts.append(f"Violation type: {v_str}")
    if ts:     parts.append(f"Video timestamp: {ts}")
    if frame:  parts.append(f"Frame number: {frame}")
    if conf is not None:
        parts.append(f"Detection confidence: {float(conf):.4f}")

    for key in ("created_at","updated_at","detected_at","date","datetime"):
        val = record.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).replace("Z","+00:00"))
                parts.append(f"{key.replace('_',' ').title()}: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                parts.append(f"Date: {dt.strftime('%Y-%m-%d')}")
            except Exception:
                parts.append(f"{key}: {val}")

    for key in ("plate_image_url","vehicle_image_url","image_url"):
        val = record.get(key)
        if val: parts.append(f"{key.replace('_',' ').title()}: {val}")

    if job: parts.append(f"Job ID: {job}")
    if rid: parts.append(f"Record ID: {rid}")

    known = {"plate_number","plate","vehicle_type","type","vehicle_id","color","status",
             "violations","timestamp","frame_number","confidence","job_id","id","_id",
             "created_at","updated_at","detected_at","date","datetime",
             "plate_image_url","vehicle_image_url","image_url"}
    for k, v in record.items():
        if k not in known and v not in (None,"",[],()):
            parts.append(f"{k.replace('_',' ').title()}: {_value_str(v)}")

    return " | ".join(parts)


def _records_to_chunks(records: List[Dict], source_id: str) -> List[ChunkMetadata]:
    chunks = []
    for i in range(0, len(records), CHUNK_RECORDS):
        batch = records[i: i + CHUNK_RECORDS]
        texts = [_record_to_text(r, i+j) for j,r in enumerate(batch)]
        combined = "\n\n".join(texts)
        chunks.append(ChunkMetadata(
            chunk_id=str(uuid.uuid4()), source_file=source_id,
            page_number=i//CHUNK_RECORDS+1, chunk_index=i//CHUNK_RECORDS,
            text=combined, char_start=0, char_end=len(combined)
        ))
    return chunks

def _embed_and_store(chunks: List[ChunkMetadata]) -> int:
    store = get_vector_store()
    total = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i+BATCH_SIZE]
        emb = embed_texts([c.text for c in batch])
        store.add_chunks(emb, batch)
        total += len(batch)
    return total

def _extract_list(data: Any, data_key: str) -> List[Dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)][:MAX_RECORDS]
    if isinstance(data, dict):
        val = data.get(data_key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)][:MAX_RECORDS]
        for key in ("data","results","records","items","vehicles","detections","violations","entries","rows","list"):
            if key in data and isinstance(data[key], list):
                return [r for r in data[key] if isinstance(r, dict)][:MAX_RECORDS]
        return [data]
    return []

def _data_summary(records: List[Dict]) -> Dict:
    if not records: return {}
    summary: Dict = {"total": len(records)}
    vtypes: Dict[str,int] = {}
    for r in records:
        vt = str(r.get("vehicle_type") or r.get("type") or "unknown").lower()
        vtypes[vt] = vtypes.get(vt,0) + 1
    if vtypes: summary["vehicle_types"] = dict(sorted(vtypes.items(), key=lambda x:-x[1]))
    viols: Dict[str,int] = {}
    for r in records:
        for v in (r.get("violations") or []):
            vs=str(v); viols[vs] = viols.get(vs,0)+1
    if viols: summary["violations"] = dict(sorted(viols.items(), key=lambda x:-x[1]))
    statuses: Dict[str,int] = {}
    for r in records:
        st=str(r.get("status") or "unknown"); statuses[st]=statuses.get(st,0)+1
    if statuses: summary["statuses"] = statuses
    confs=[float(r["confidence"]) for r in records if r.get("confidence") is not None]
    if confs: summary["confidence"]={"avg":round(sum(confs)/len(confs),3),"min":round(min(confs),3),"max":round(max(confs),3)}
    dates=[]
    for r in records:
        for dk in ("created_at","updated_at","date"):
            v=r.get(dk)
            if v:
                try: dates.append(datetime.fromisoformat(str(v).replace("Z","+00:00"))); break
                except: pass
    if dates: summary["date_range"]={"from":min(dates).strftime("%Y-%m-%d %H:%M"),"to":max(dates).strftime("%Y-%m-%d %H:%M")}
    plates={r.get("plate_number") or r.get("plate") for r in records}; plates.discard(None)
    summary["unique_plates"]=len(plates)
    return summary

def _suggested_questions(fields: List[str], summary: Dict) -> List[str]:
    qs=["How many records are in the dataset?"]
    f=set(fields)
    if "vehicle_type" in f or "type" in f:
        types=list((summary.get("vehicle_types") or {}).keys())
        if types: qs.append(f"How many {types[0]}s were detected?")
        if len(types)>1: qs.append(f"Show all {types[1]}s with their plate numbers.")
        qs += ["Which vehicle type appears most frequently?","List all trucks detected.","Show me all buses.","How many cycles were detected?"]
    if "plate_number" in f or "plate" in f:
        qs += ["What are the unique plate numbers detected?","How many unique vehicles were detected?"]
    if "violations" in f:
        qs += ["List all ANPR violations.","Which vehicles have violations?","Show records with traffic violations."]
    if "status" in f:
        statuses=list((summary.get("statuses") or {}).keys())
        if "verified" in statuses: qs.append("Show only verified records.")
        qs.append("How many records are verified?")
    if "confidence" in f:
        qs += ["Which records have confidence above 0.5?","Show records with low detection confidence.","What is the average detection confidence?"]
    if "created_at" in f or "date" in f:
        dr=summary.get("date_range") or {}
        if dr: qs.append(f"Show records from {dr.get('from','today')[:10]}.")
        qs += ["Show the most recent detections.","What was detected today?"]
    if "job_id" in f: qs += ["List all job IDs in the dataset.","How many different jobs are recorded?"]
    seen=set(); unique=[]
    for q in qs:
        k=q.lower()[:50]
        if k not in seen: seen.add(k); unique.append(q)
    return unique[:10]

async def _fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent":"Mozilla/5.0 MR-AI-RAG/2.0","Accept":"application/json, */*"})
    resp.raise_for_status()
    return resp.json()


@router.post("/preview-json-url", response_model=PreviewResponse, summary="Preview JSON API without indexing")
async def preview_json_url(req: PreviewRequest, _key: dict = Depends(require_api_key)):
    url=req.url.strip()
    if not url.startswith("http"): raise HTTPException(400,"URL must start with http:// or https://")
    try: raw=await _fetch_json(url)
    except httpx.TimeoutException: raise HTTPException(504,f"Request timed out: {url}")
    except httpx.HTTPStatusError as e: raise HTTPException(502,f"HTTP {e.response.status_code} from server")
    except Exception as e: raise HTTPException(502,f"Failed to fetch: {e}")
    records=_extract_list(raw,req.data_key)
    if not records: raise HTTPException(422,f"No records list found. Keys: {list(raw.keys()) if isinstance(raw,dict) else 'N/A'}")
    fields=sorted(records[0].keys()) if records else []
    sample={k:str(v)[:100] for k,v in list(records[0].items())[:10]}
    summary=_data_summary(records)
    return PreviewResponse(success=True,total_records=len(records),fields_detected=fields,sample_record=sample,data_summary=summary,suggested_questions=_suggested_questions(fields,summary))


@router.post("/ingest-json-url", response_model=JsonIngestResponse, summary="Fetch JSON URL and index for RAG")
async def ingest_json_url(req: JsonUrlRequest, _key: dict = Depends(require_api_key)):
    url=req.url.strip()
    if not url.startswith("http"): raise HTTPException(400,"URL must start with http:// or https://")
    label=req.label.strip() or urlparse(url).netloc or "JSON API Data"
    source_id=f"json/{re.sub(r'[^a-zA-Z0-9_-]','_',url)[:80]}"
    logger.info(f"[JSON] Fetching: {url}")
    try: raw=await _fetch_json(url)
    except httpx.TimeoutException: raise HTTPException(504,f"Timed out: {url}")
    except httpx.HTTPStatusError as e: raise HTTPException(502,f"HTTP {e.response.status_code}")
    except Exception as e: raise HTTPException(502,f"Fetch failed: {e}")
    records=_extract_list(raw,req.data_key)
    if not records: raise HTTPException(422,f"No records. Keys: {list(raw.keys()) if isinstance(raw,dict) else 'N/A'}")
    chunks=_records_to_chunks(records,source_id)
    if not chunks: raise HTTPException(422,"Could not build chunks.")
    stored=_embed_and_store(chunks)
    _raw_records[source_id]=records
    fields=sorted(records[0].keys()) if records else []
    _raw_meta[source_id]={"label":label,"url":url,"fields":fields,"total":len(records)}
    sample={k:str(v)[:80] for k,v in list(records[0].items())[:8]}
    logger.info(f"[JSON] Done — {stored} chunks for '{label}'")
    return JsonIngestResponse(success=True,source_id=source_id,label=label,url=url,total_records=len(records),total_chunks=stored,fields_detected=fields,sample_record=sample,message=f"Indexed {len(records)} records ({stored} chunks) from '{label}'")


@router.post("/ingest-json-file", response_model=JsonIngestResponse, summary="Upload JSON file and index for RAG")
async def ingest_json_file(file: UploadFile=File(...), data_key: str="data", _key: dict = Depends(require_api_key)):
    if not file.filename: raise HTTPException(400,"No file provided.")
    if not file.filename.lower().endswith(".json"): raise HTTPException(400,"Only .json files accepted.")
    content=await file.read()
    if len(content)>50*1024*1024: raise HTTPException(413,"File exceeds 50MB.")
    try: raw=json.loads(content)
    except json.JSONDecodeError as e: raise HTTPException(422,f"Invalid JSON: {e}")
    label=file.filename
    source_id=f"json/{re.sub(r'[^a-zA-Z0-9_-]','_',file.filename)[:80]}"
    records=_extract_list(raw,data_key)
    if not records: raise HTTPException(422,"No records list found.")
    chunks=_records_to_chunks(records,source_id)
    stored=_embed_and_store(chunks)
    _raw_records[source_id]=records
    fields=sorted(records[0].keys()) if records else []
    _raw_meta[source_id]={"label":label,"url":"","fields":fields,"total":len(records)}
    sample={k:str(v)[:80] for k,v in list(records[0].items())[:8]}
    return JsonIngestResponse(success=True,source_id=source_id,label=label,url="",total_records=len(records),total_chunks=stored,fields_detected=fields,sample_record=sample,message=f"Indexed {len(records)} records ({stored} chunks) from '{label}'")


@router.get("/json-records", response_model=RecordsResponse, summary="Get raw records for data viewer")
async def get_json_records(source_id: str=Query(...), limit: int=Query(500,ge=1,le=2000), offset: int=Query(0,ge=0), _key: dict = Depends(require_api_key)):
    records=_raw_records.get(source_id)
    if records is None: raise HTTPException(404,f"No records for '{source_id}'. Re-index the data.")
    meta=_raw_meta.get(source_id,{})
    return RecordsResponse(source_id=source_id,label=meta.get("label",source_id),total=len(records),fields=meta.get("fields",[]),records=records[offset:offset+limit])