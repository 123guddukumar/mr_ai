import httpx
import os

def test_pollinations_flux():
    prompt = "Ancient church archive with “Index of Forbidden Books”, old forbidden manuscripts on wooden table, secret readers hiding books under cloaks, candlelit gothic library, mysterious cinematic atmosphere"
    # Pollinations AI Flux URL
    url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true&model=flux"
    
    print(f"Generating image for: '{prompt}'...")
    print(f"URL: {url}")
    
    try:
        response = httpx.get(url, timeout=60.0)
        if response.status_code == 200:
            filename = "flux_test_image.jpg"
            with open(filename, "wb") as f:
                f.write(response.content)
            print(f"✅ Success! Image saved as '{filename}' in your current directory.")
            print(f"Open the file to see the Flux magic!")
        else:
            print(f"❌ Error: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    test_pollinations_flux()
