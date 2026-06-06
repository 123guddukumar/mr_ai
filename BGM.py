import re
import requests

def extract_lqmp3(url):
    """Extract lqmp3 directly from page source"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    try:
        print(f"Fetching BGM URL: {url}")
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=15)
        
        print(f"Response status: {response.status_code}")
        
        # Check if we got the actual page or just cookie banner
        if 'cookie' in response.text.lower() and len(response.text) < 50000:
            print("Got cookie banner, trying with cookies...")
            # Get cookies first
            session.get('https://www.epidemicsound.com', headers=headers)
            response = session.get(url, headers=headers)
        
        # Multiple patterns to find lqmp3 (supporting escaped slashes like \/)
        patterns = [
            r'"lqMp3Url"\s*:\s*"(https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"]+\.mp3)"',
            r'https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"\s\\/]+\.mp3'
        ]
        
        for i, pattern in enumerate(patterns):
            matches = re.findall(pattern, response.text)
            if matches:
                matched_url = matches[0] if isinstance(matches[0], str) else matches[0]
                lqmp3 = matched_url.replace(r'\/', '/').replace('\\/', '/').replace('\\', '')
                print(f"Found using pattern {i+1}")
                return lqmp3
        
        # Save response for debugging
        with open('debug_response.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        print("Saved response to debug_response.html for inspection")
        
        return None
        
    except Exception as e:
        print(f"Error extracting LQMP3: {e}")
        return None

if __name__ == "__main__":
    # Main execution
    print("="*60)
    print("LQMP3 Link Extractor")
    print("="*60)

    url = input("\nEnter URL: ").strip()
    if not url.startswith('http'):
        url = 'https://' + url

    result = extract_lqmp3(url)

    print("\n" + "="*60)
    if result:
        print("LQMP3 LINK FOUND:\n")
        print(result)
        print("\n" + "="*60)
        
        # Download option
        dl = input("\nDownload MP3? (y/n): ").lower()
        if dl == 'y':
            print("\nDownloading...")
            mp3_response = requests.get(result, stream=True)
            if mp3_response.status_code == 200:
                filename = result.split('/')[-1] + '.mp3'
                with open(filename, 'wb') as f:
                    for chunk in mp3_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"Downloaded: {filename}")
            else:
                print("Download failed")
    else:
        print("No LQMP3 link found")
        print("\nTip: Try using the previous script that was working")
    print("="*60)