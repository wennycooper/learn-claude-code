# Unsplash Image Integration

Add relevant photos to slides using the Unsplash API.

## Setup

Requires `UNSPLASH_ACCESS_KEY` in `.env`. Free tier: 50 requests/hour.

## Workflow

### Step 1: Pick keywords per slide

For each slide, extract 1-2 keyword phrases that describe the visual concept.
Think like a photo editor: what image would complement this slide's message?

| Slide topic | Good keyword | Bad keyword |
|-------------|-------------|-------------|
| 月臺門系統 | "train platform door" | "system" |
| 維修保養 | "engineer maintenance railway" | "maintenance" |
| 安全規範 | "safety industrial worker" | "safety" |
| 數據統計 | "data analytics dashboard" | "data" |

### Step 2: Download image

```bash
python scripts/unsplash.py "train platform door" --out images/slide_02.jpg
python scripts/unsplash.py "engineer maintenance" --out images/slide_03.jpg --orientation landscape
```

The script prints the photographer credit — save it for the slide footer.

### Step 3: Insert image into slide (python-pptx)

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN

prs = Presentation("output.pptx")
slide = prs.slides[1]  # 0-indexed

# Half-bleed right: image fills right half of slide
pic = slide.shapes.add_picture(
    "images/slide_02.jpg",
    left=Inches(5.0),   # start at center
    top=Inches(0),
    width=Inches(5.0),  # fill right half
    height=Inches(7.5), # full height
)

# Send image to back so text stays on top
slide.shapes._spTree.remove(pic._element)
slide.shapes._spTree.insert(2, pic._element)

prs.save("output.pptx")
```

### Step 4: Add photo credit

Always credit the photographer. Add a small caption (10pt, muted color) at the
bottom of the image or in the slide footer:

```python
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

txBox = slide.shapes.add_textbox(Inches(5.05), Inches(7.1), Inches(4.9), Inches(0.3))
tf = txBox.text_frame
tf.text = "Photo by Jane Smith on Unsplash"
tf.paragraphs[0].runs[0].font.size = Pt(8)
tf.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
```

## Common Layout Patterns

### Half-bleed image (recommended)
```
┌─────────────────────────────────┐
│  Title                          │
│                                 │
│  • Bullet 1    │ [  IMAGE  ]    │
│  • Bullet 2    │               │
│  • Bullet 3    │               │
└─────────────────────────────────┘
  left: text       right: image
```
Image position: `left=Inches(5.0), top=0, width=Inches(5.0), height=Inches(7.5)`

### Background image with overlay
```
┌─────────────────────────────────┐
│░░░░░░░░[  IMAGE  ]░░░░░░░░░░░░░│
│░  Title (white on dark bg)  ░░░│
│░  Subtitle                  ░░░│
└─────────────────────────────────┘
```
Image position: `left=0, top=0, width=Inches(10), height=Inches(7.5)`
Then add a semi-transparent rectangle overlay, then text on top.

### Top image strip
```
┌─────────────────────────────────┐
│████████ [IMAGE STRIP] ██████████│  ~2.5" tall
├─────────────────────────────────┤
│  Title                          │
│  Content...                     │
└─────────────────────────────────┘
```
Image position: `left=0, top=0, width=Inches(10), height=Inches(2.5)`

## Tips

- Use `--orientation landscape` for most slides (matches 16:9 ratio)
- Use `--size regular` (1080px) — large enough for slides, not too heavy
- Create `images/` folder before running the script: `mkdir -p images`
- One image per slide is usually enough — more looks cluttered
- Avoid images with text in them (conflicts with your slide text)
- Dark images work well for title/conclusion slides with white text overlay
