#!/usr/bin/env python3
"""
unsplash.py - Search and download images from Unsplash

Usage:
    python scripts/unsplash.py "mountain landscape" --out slide_bg.jpg
    python scripts/unsplash.py "technology abstract" --out img.jpg --orientation landscape
    python scripts/unsplash.py "city skyline" --out img.jpg --size regular

Sizes: thumb(200px), small(400px), regular(1080px), full(original)
Orientations: landscape, portrait, squarish

Requires: UNSPLASH_ACCESS_KEY in environment or .env file
"""

import argparse
import os
import sys
import urllib.request
import urllib.parse
import json
from pathlib import Path

def load_env():
    """Load .env file from cwd or parent directories."""
    for d in [Path.cwd()] + list(Path.cwd().parents)[:3]:
        env_file = d / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break

def search_unsplash(query: str, orientation: str = "landscape", count: int = 1) -> list:
    """Search Unsplash and return list of photo dicts."""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not access_key:
        print("Error: UNSPLASH_ACCESS_KEY not set in environment or .env", file=sys.stderr)
        sys.exit(1)

    params = urllib.parse.urlencode({
        "query": query,
        "per_page": count,
        "orientation": orientation,
    })
    url = f"https://api.unsplash.com/search/photos?{params}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Client-ID {access_key}",
        "Accept-Version": "v1",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    results = []
    for photo in data.get("results", []):
        results.append({
            "id": photo["id"],
            "description": photo.get("alt_description") or photo.get("description") or query,
            "photographer": photo["user"]["name"],
            "urls": photo["urls"],
            "credit": f"Photo by {photo['user']['name']} on Unsplash",
        })
    return results


def download_image(url: str, out_path: str) -> str:
    """Download image to file, return path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, out)
    return str(out)


def main():
    load_env()

    parser = argparse.ArgumentParser(description="Search and download Unsplash images")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--out", required=True, help="Output file path (e.g. img.jpg)")
    parser.add_argument("--size", default="regular",
                        choices=["thumb", "small", "regular", "full"],
                        help="Image size (default: regular = 1080px wide)")
    parser.add_argument("--orientation", default="landscape",
                        choices=["landscape", "portrait", "squarish"],
                        help="Image orientation (default: landscape)")

    args = parser.parse_args()

    photos = search_unsplash(args.query, orientation=args.orientation)
    if not photos:
        print(f"No results for: {args.query}", file=sys.stderr)
        sys.exit(1)

    photo = photos[0]
    img_url = photo["urls"][args.size]
    path = download_image(img_url, args.out)

    # Print metadata for agent to record
    print(f"Downloaded: {path}")
    print(f"Credit: {photo['credit']}")
    print(f"Description: {photo['description']}")


if __name__ == "__main__":
    main()
