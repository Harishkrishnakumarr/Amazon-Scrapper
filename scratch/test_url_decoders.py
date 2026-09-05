import re
import base64
from urllib.parse import urlparse, parse_qs, unquote

def decode_search_url(href: str) -> str:
    if not href:
        return ""
    
    # 1. Bing /ck/a? redirection
    if "bing.com/ck/a?" in href or "/ck/a?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        u_val = qs.get("u", [""])[0]
        if u_val:
            b64_str = u_val
            if b64_str.startswith("a1"):
                b64_str = b64_str[2:]
            padding = len(b64_str) % 4
            if padding:
                b64_str += "=" * (4 - padding)
            try:
                decoded = base64.urlsafe_b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    # 2. Yahoo /RU= redirection
    if "r.search.yahoo.com" in href and "/RU=" in href:
        m = re.search(r"/RU=([^/]+)/", href)
        if m:
            try:
                decoded = unquote(m.group(1))
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    # 3. Google /url?q= redirection
    if "google.com/url?" in href or "/url?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        q_val = qs.get("q", [""])[0] or qs.get("url", [""])[0]
        if q_val and q_val.startswith("http"):
            return q_val

    # 4. DuckDuckGo /l/?uddg= redirection
    if "duckduckgo.com/l/?" in href or "/l/?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [""])[0]
        if uddg:
            try:
                decoded = unquote(uddg)
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    return href

print("Testing decode_search_url:")
bing_sample = "https://www.bing.com/ck/a?!&&p=1a9fd735a5784d1c3946fed88704ebfb35fd5fd91b0df93206fac9287bd8904cJmltdHM9MTc4NjU3OTIwMA&ptn=3&ver=2&hsh=4&fclid=21a9de68-e56c-62a1-3246-c9dde467639e&u=a1aHR0cHM6Ly9jbGVhcnRheC5pbi9nc3QtbnVtYmVyLXNlYXJjaC8&ntb=1"
print(f"Decoded Bing: {decode_search_url(bing_sample)}")
assert decode_search_url(bing_sample) == "https://cleartax.in/gst-number-search/"

yahoo_sample = "https://r.search.yahoo.com/_ylt=Awr.123/RU=https%3a%2f%2fcleartax.in%2fgst/RK=2/RS=456"
print(f"Decoded Yahoo: {decode_search_url(yahoo_sample)}")
assert decode_search_url(yahoo_sample) == "https://cleartax.in/gst"

google_sample = "https://www.google.com/url?q=https://cleartax.in/gst&sa=U"
print(f"Decoded Google: {decode_search_url(google_sample)}")
assert decode_search_url(google_sample) == "https://cleartax.in/gst"

print("All decoding tests passed!")
