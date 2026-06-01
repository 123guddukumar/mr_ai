with open('frontend/dashboard.html', encoding='utf-8') as f:
    for idx, line in enumerate(f, 1):
        if "tab === 'effects'" in line or "tab === 'music'" in line or "tab === 'audio'" in line or "tab === 'speed'" in line:
            print(f'{idx}: {line.strip()}')
