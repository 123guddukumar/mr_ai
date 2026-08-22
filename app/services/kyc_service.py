"""
MR AI RAG - KYC Verification Service
Provides mock + extensible functions for Aadhar, PAN, and CIBIL verification.
Replace the mock logic with actual API calls once production credentials are ready.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def verify_aadhar(aadhar_number: str) -> dict:
    """
    Verify an Aadhar card number.
    Format: 12 digits, not starting with 0 or 1.
    Replace body with actual UIDAI / third-party API call in production.
    """
    aadhar_clean = re.sub(r"[\s\-]", "", aadhar_number or "")
    if not aadhar_clean.isdigit():
        return {"valid": False, "status": "rejected", "reason": "Aadhar number must contain only digits."}
    if len(aadhar_clean) != 12:
        return {"valid": False, "status": "rejected", "reason": f"Aadhar must be 12 digits, got {len(aadhar_clean)}."}
    if aadhar_clean[0] in ("0", "1"):
        return {"valid": False, "status": "rejected", "reason": "Aadhar number cannot start with 0 or 1."}

    # ── Production: Replace with real UIDAI API call ──────────────────────────
    # Example: POST https://kyc-api.example.com/v1/aadhar/verify
    # payload = {"aadhar": aadhar_clean}
    # response = requests.post(url, json=payload, headers=auth_headers)
    # return response.json()
    # ─────────────────────────────────────────────────────────────────────────

    logger.info(f"Aadhar verification (mock): {aadhar_clean[:4]}XXXX{aadhar_clean[-4:]}")
    return {
        "valid": True,
        "status": "verified",
        "aadhar_last4": aadhar_clean[-4:],
        "name_match": True,
        "message": "Aadhar number verified successfully.",
        "mode": "mock"
    }


def verify_pan(pan_number: str) -> dict:
    """
    Verify a PAN card number.
    Format: 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F).
    Replace body with actual Income Tax Dept / third-party API call in production.
    """
    pan_clean = (pan_number or "").strip().upper()
    pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
    if not re.match(pattern, pan_clean):
        return {
            "valid": False,
            "status": "rejected",
            "reason": f"Invalid PAN format. Expected format: ABCDE1234F, got '{pan_clean}'."
        }

    # ── Production: Replace with real PAN verification API call ──────────────
    # Example: POST https://kyc-api.example.com/v1/pan/verify
    # payload = {"pan": pan_clean}
    # response = requests.post(url, json=payload, headers=auth_headers)
    # return response.json()
    # ─────────────────────────────────────────────────────────────────────────

    logger.info(f"PAN verification (mock): {pan_clean[:3]}XXXX{pan_clean[-2:]}")
    return {
        "valid": True,
        "status": "verified",
        "pan": pan_clean,
        "pan_type": _get_pan_type(pan_clean[3]),
        "name_match": True,
        "message": "PAN number verified successfully.",
        "mode": "mock"
    }


def check_cibil_score(pan_number: str, dob: Optional[str] = None) -> dict:
    """
    Check CIBIL credit score for a given PAN number.
    Replace body with actual CIBIL / TransUnion API call in production.
    """
    pan_clean = (pan_number or "").strip().upper()
    pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
    if not re.match(pattern, pan_clean):
        return {
            "valid": False,
            "status": "rejected",
            "reason": "Invalid PAN format for CIBIL lookup.",
            "score": None
        }

    # ── Production: Replace with real CIBIL / bureau API call ────────────────
    # Example: POST https://api.cibil.com/v1/score
    # payload = {"pan": pan_clean, "dob": dob}
    # response = requests.post(url, json=payload, headers=auth_headers)
    # return response.json()
    # ─────────────────────────────────────────────────────────────────────────

    # Mock: generate a deterministic score from the PAN digits (for testing consistency)
    pan_digits = "".join(c for c in pan_clean if c.isdigit())
    mock_score = 650 + (int(pan_digits[:2]) % 150) if pan_digits else 700

    logger.info(f"CIBIL score check (mock): PAN={pan_clean[:3]}XXXX{pan_clean[-2:]}, score={mock_score}")
    return {
        "valid": True,
        "status": "success",
        "pan": pan_clean,
        "score": mock_score,
        "rating": _score_to_rating(mock_score),
        "loan_eligible": mock_score >= 650,
        "message": f"CIBIL score retrieved: {mock_score}",
        "mode": "mock"
    }


def run_full_kyc(aadhar: Optional[str] = None, pan: Optional[str] = None,
                 dob: Optional[str] = None, cibil_threshold: int = 650) -> dict:
    """
    Convenience wrapper: runs all provided checks and returns a combined result.
    Returns a consolidated status: verified | rejected | partial
    """
    result = {"aadhar": None, "pan": None, "cibil": None, "overall_status": "n/a"}
    all_passed = True

    if aadhar:
        result["aadhar"] = verify_aadhar(aadhar)
        if not result["aadhar"].get("valid"):
            all_passed = False

    if pan:
        result["pan"] = verify_pan(pan)
        if not result["pan"].get("valid"):
            all_passed = False

        # Run CIBIL only if PAN is valid
        if result["pan"].get("valid"):
            result["cibil"] = check_cibil_score(pan, dob)
            if result["cibil"].get("score") and result["cibil"]["score"] < cibil_threshold:
                all_passed = False
                result["cibil"]["loan_eligible"] = False

    if not aadhar and not pan:
        result["overall_status"] = "n/a"
    elif all_passed:
        result["overall_status"] = "verified"
    elif any(v is not None for v in [result["aadhar"], result["pan"]]):
        result["overall_status"] = "partial" if (aadhar and pan) else "rejected"
    else:
        result["overall_status"] = "rejected"

    return result


# ── Private Helpers ────────────────────────────────────────────────────────────

def _get_pan_type(fourth_char: str) -> str:
    """Decode PAN holder type from 4th character."""
    types = {
        "P": "Individual",
        "C": "Company",
        "H": "HUF (Hindu Undivided Family)",
        "F": "Firm",
        "A": "AOP (Association of Persons)",
        "T": "AOP (Trust)",
        "B": "BOI (Body of Individuals)",
        "L": "Local Authority",
        "J": "Artificial Juridical Person",
        "G": "Government",
    }
    return types.get(fourth_char.upper(), "Unknown")


def _score_to_rating(score: int) -> str:
    if score >= 800:
        return "Excellent"
    elif score >= 750:
        return "Very Good"
    elif score >= 700:
        return "Good"
    elif score >= 650:
        return "Fair"
    else:
        return "Poor"
