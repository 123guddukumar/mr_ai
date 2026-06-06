import re

# Mock page source from Epidemic Sound Next.js data
mock_html = """
<html>
<body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"track":{"id":"12345","title":"Upbeat Tune","lqMp3Url":"https:\\/\\/audiocdn.epidemicsound.com\\/lqmp3\\/4PNDU6Gv5G.mp3"}}}}
</script>
</body>
</html>
"""

patterns = [
    r'"lqMp3Url"\s*:\s*"(https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"]+\.mp3)"',
    r'https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"\s\\/]+\.mp3'
]

print("Running Regex Test:")
for i, pattern in enumerate(patterns):
    matches = re.findall(pattern, mock_html)
    if matches:
        matched_url = matches[0]
        cleaned_url = matched_url.replace(r'\/', '/').replace('\\/', '/').replace('\\', '')
        print(f"Pattern {i+1} matched: {matched_url}")
        print(f"Cleaned URL: {cleaned_url}")
        assert cleaned_url == "https://audiocdn.epidemicsound.com/lqmp3/4PNDU6Gv5G.mp3"
        print("SUCCESS")
        break
else:
    print("FAILED: No pattern matched")
