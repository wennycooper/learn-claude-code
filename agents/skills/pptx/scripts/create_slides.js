#!/usr/bin/env node
/**
 * create_slides.js — Create a beautiful .pptx from a JSON content file.
 * Images fetched via SerpAPI Google Images (no local download needed).
 *
 * Usage:
 *   node skills/pptx/scripts/create_slides.js slides_content.json --out output.pptx
 *
 * JSON format:
 *   {
 *     "title": "Presentation Title",
 *     "subtitle": "Optional subtitle",
 *     "color": "1B2A4A",          // optional accent hex, default dark navy
 *     "slides": [
 *       {
 *         "title": "Slide Title",
 *         "bullets": ["Point 1", "Point 2", "Point 3"],
 *         "image_keyword": "鄭麗文習近平握手"
 *       }
 *     ]
 *   }
 *
 * Requires: npm install pptxgenjs
 *           SERP_API_KEY in .env
 */

const fs   = require("fs");
const path = require("path");
const https = require("https");
const http  = require("http");
const url   = require("url");

// ── Load .env ────────────────────────────────────────────────────────────────
function loadEnv() {
  let dir = process.cwd();
  for (let i = 0; i < 5; i++) {
    const envFile = path.join(dir, ".env");
    if (fs.existsSync(envFile)) {
      const lines = fs.readFileSync(envFile, "utf8").split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
        const [k, ...rest] = trimmed.split("=");
        if (!process.env[k.trim()]) process.env[k.trim()] = rest.join("=").trim();
      }
      break;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
}

// ── HTTP download helper ──────────────────────────────────────────────────────
function downloadBytes(imgUrl) {
  return new Promise((resolve) => {
    const parsed = new url.URL(imgUrl);
    const lib = parsed.protocol === "https:" ? https : http;
    const req = lib.get(imgUrl, { headers: { "User-Agent": "Mozilla/5.0" }, timeout: 12000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return downloadBytes(res.headers.location).then(resolve);
      }
      if (res.statusCode !== 200) return resolve(null);
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
  });
}

function isWebp(buf) {
  return buf.length > 12 && buf.slice(0, 4).toString() === "RIFF" && buf.slice(8, 12).toString() === "WEBP";
}

function bufToBase64(buf) {
  // Detect mime type
  if (buf[0] === 0xff && buf[1] === 0xd8) return "image/jpeg;base64," + buf.toString("base64");
  if (buf.slice(0, 8).toString("hex") === "89504e470d0a1a0a") return "image/png;base64," + buf.toString("base64");
  return "image/jpeg;base64," + buf.toString("base64"); // fallback assume jpeg
}

// ── SerpAPI image search + download ──────────────────────────────────────────
async function fetchImageData(query) {
  const apiKey = process.env.SERP_API_KEY || "";
  if (!apiKey) {
    console.error("  [skip image] SERP_API_KEY not set");
    return null;
  }
  const params = new URLSearchParams({
    engine: "google_images", q: query, api_key: apiKey, num: "10", safe: "active",
  });

  const results = await new Promise((resolve) => {
    https.get(`https://serpapi.com/search.json?${params}`, { timeout: 15000 }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => {
        try { resolve(JSON.parse(data).images_results || []); }
        catch { resolve([]); }
      });
    }).on("error", () => resolve([]));
  });

  if (!results.length) {
    console.error(`  [no image] no search results for '${query}'`);
    return null;
  }

  for (const r of results) {
    for (const imgUrl of [r.original, r.thumbnail].filter(Boolean)) {
      if (!/^https?:\/\//.test(imgUrl)) continue;
      const buf = await downloadBytes(imgUrl);
      if (!buf || buf.length < 1000) continue;
      if (isWebp(buf)) { continue; } // skip WEBP
      const src = r.source || "Google Images";
      console.log(`  [image] '${query}' → via Google / ${src}`);
      return { data: bufToBase64(buf), credit: `via Google / ${src}` };
    }
  }
  console.error(`  [no image] all downloads failed for '${query}'`);
  return null;
}

// ── Hex color helper ─────────────────────────────────────────────────────────
function sanitizeColor(hex) {
  return hex.replace(/^#/, "").toUpperCase();
}

function darken(hex, pct = 0.4) {
  const h = sanitizeColor(hex);
  const r = Math.floor(parseInt(h.slice(0, 2), 16) * (1 - pct));
  const g = Math.floor(parseInt(h.slice(2, 4), 16) * (1 - pct));
  const b = Math.floor(parseInt(h.slice(4, 6), 16) * (1 - pct));
  return [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
}

// ── Slide builders ───────────────────────────────────────────────────────────
const W = 10, H = 5.625; // 16x9 inches

function addTitleSlide(pres, title, subtitle, accent, imgData) {
  const slide = pres.addSlide();
  slide.background = { color: darken(accent, 0.3) };

  if (imgData) {
    slide.addImage({
      data: imgData.data,
      x: W * 0.45, y: 0, w: W * 0.55, h: H,
      sizing: { type: "cover", w: W * 0.55, h: H },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: W * 0.45, y: 0, w: W * 0.55, h: H,
      fill: { color: "000000", transparency: 45 },
      line: { color: "000000", transparency: 100 },
    });
  }

  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 0.08, h: H,
    fill: { color: sanitizeColor(accent) },
    line: { color: sanitizeColor(accent) },
  });

  slide.addText(title, {
    x: 0.35, y: H * 0.28, w: W * 0.5, h: 1.4,
    fontSize: 36, bold: true, color: "FFFFFF", fontFace: "Calibri", wrap: true,
    shadow: { type: "outer", color: "000000", opacity: 0.5, blur: 8, offset: 3, angle: 135 },
  });

  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.35, y: H * 0.62, w: W * 0.5, h: 0.6,
      fontSize: 16, color: "CCCCCC", fontFace: "Calibri",
    });
  }

  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: H - 0.28, w: W, h: 0.28,
    fill: { color: "000000", transparency: 55 },
    line: { color: "000000", transparency: 100 },
  });
  slide.addText(imgData ? imgData.credit : "", {
    x: 0.15, y: H - 0.26, w: W - 0.3, h: 0.22,
    fontSize: 7, color: "AAAAAA", align: "right",
  });
}

function addContentSlide(pres, title, bullets, accent, imgData, slideNum) {
  const slide = pres.addSlide();
  slide.background = { color: "F5F6FA" };

  const hasImg = !!imgData;
  const imgX    = W * 0.505;
  const imgW    = W * 0.495;
  const contentW = hasImg ? W * 0.48 : W - 0.4;

  if (hasImg) {
    slide.addImage({
      data: imgData.data,
      x: imgX, y: 0, w: imgW, h: H,
      sizing: { type: "cover", w: imgW, h: H },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: imgX, y: 0, w: 0.3, h: H,
      fill: { color: "F5F6FA", transparency: 20 },
      line: { color: "F5F6FA", transparency: 100 },
    });
    slide.addText(imgData.credit, {
      x: imgX + 0.1, y: H - 0.22, w: imgW - 0.2, h: 0.2,
      fontSize: 6.5, color: "AAAAAA", align: "right",
    });
  }

  // Title bar
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: hasImg ? W * 0.505 : W, h: 0.95,
    fill: { color: sanitizeColor(accent) },
    line: { color: sanitizeColor(accent) },
  });

  // Slide number badge
  slide.addShape(pres.shapes.OVAL, {
    x: contentW - 0.05, y: 0.18, w: 0.55, h: 0.55,
    fill: { color: darken(accent, 0.25) },
    line: { color: darken(accent, 0.25) },
  });
  slide.addText(String(slideNum), {
    x: contentW - 0.05, y: 0.18, w: 0.55, h: 0.55,
    fontSize: 13, bold: true, color: "FFFFFF", align: "center", valign: "middle",
  });

  // Title text
  slide.addText(title, {
    x: 0.25, y: 0.08, w: contentW - 0.4, h: 0.8,
    fontSize: 22, bold: true, color: "FFFFFF", fontFace: "Calibri", valign: "middle", margin: 0,
  });

  // Content card
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.2, y: 1.1, w: contentW, h: H - 1.3,
    fill: { color: "FFFFFF" },
    line: { color: "E0E4EE" },
    shadow: { type: "outer", color: "000000", opacity: 0.08, blur: 10, offset: 3, angle: 135 },
  });

  const bulletItems = bullets.map((b, i) => ({
    text: b,
    options: {
      bullet: { code: "25A0", color: sanitizeColor(accent) },
      color: "333333", fontSize: 14, fontFace: "Calibri",
      breakLine: i < bullets.length - 1, paraSpaceAfter: 6,
    },
  }));

  slide.addText(bulletItems, {
    x: 0.38, y: 1.22, w: contentW - 0.36, h: H - 1.55,
    valign: "top", wrap: true,
  });
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  loadEnv();

  const args = process.argv.slice(2);
  const contentFile = args[0];
  const outIdx = args.indexOf("--out");
  const outFile = outIdx >= 0 ? args[outIdx + 1] : "output.pptx";

  if (!contentFile) {
    console.error("Usage: node create_slides.js content.json --out output.pptx");
    process.exit(1);
  }

  const data = JSON.parse(fs.readFileSync(contentFile, "utf8"));
  const accent = sanitizeColor(data.color || "1B2A4A");
  const slideList = data.slides || data.content || [];

  if (!slideList.length) {
    console.error("ERROR: JSON has no 'slides' array.");
    process.exit(1);
  }

  const pptxgen = require("pptxgenjs");
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.title = data.title || "Presentation";

  // Title slide
  const mainTitle = data.title || "Presentation";
  const mainKw = data.image_keyword || mainTitle;
  console.log(`[title slide] '${mainTitle}'`);
  const titleImg = await fetchImageData(mainKw);
  addTitleSlide(pres, mainTitle, data.subtitle || "", accent, titleImg);

  // Content slides
  for (let i = 0; i < slideList.length; i++) {
    const sd = slideList[i];
    const stitle  = sd.title || `Slide ${i + 1}`;
    const bullets = sd.bullets || [];
    const kw      = sd.image_keyword || stitle;
    console.log(`[slide ${i + 1}] '${stitle}' — searching '${kw}'`);
    const imgData = await fetchImageData(kw);
    addContentSlide(pres, stitle, bullets, accent, imgData, i + 1);
  }

  await pres.writeFile({ fileName: outFile });
  console.log(`\nSaved: ${outFile}  (${1 + slideList.length} slides)`);
}

main().catch((e) => {
  console.error("Fatal:", e.message);
  process.exit(1);
});
