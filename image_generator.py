"""
PAWSENSE AI — Image Generation Engine
Supports:
1. Google Imagen 3 (via Generative Language API)
2. Pollinations.ai (Free high-resolution Flux/SDXL Diffusion)
3. High-Fidelity Procedural Pillow/SVG Pet Graphic Generator (Offline fallback)
"""

import os
import re
import secrets
import random
import urllib.parse
import base64
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


IMAGE_STYLES = {
    'photorealistic': {
        'name': 'Photorealistic',
        'suffix': 'award-winning studio pet photography, ultra-detailed fur texture, 8k resolution, soft natural lighting, crisp focus, cinematic masterpiece'
    },
    'cute-3d': {
        'name': 'Cute 3D Pixar',
        'suffix': 'adorable 3D Pixar Disney style character, vibrant warm colors, smooth soft fur, big expressive eyes, Unreal Engine 5 render, playful whimsical pet animation'
    },
    'watercolor': {
        'name': 'Artistic Watercolor',
        'suffix': 'gorgeous vibrant watercolor and ink illustration, soft pastel washes, fine artistic brush strokes, expressive animal fine art'
    },
    'clinical': {
        'name': 'Veterinary Infographic',
        'suffix': 'clean educational veterinary medical diagram, anatomically accurate pet illustration, clear labels, professional veterinary science visualization'
    },
    'veterinary': {
        'name': 'Veterinary Infographic',
        'suffix': 'clean educational veterinary medical diagram, anatomically accurate pet illustration, clear labels, professional veterinary science visualization'
    },
    'fantasy': {
        'name': 'Fantasy & Heroic',
        'suffix': 'epic fantasy heroic pet portrait, wearing miniature ornate armor or magical cape, glowing mystical lighting, highly detailed digital painting'
    }
}


def detect_image_intent(text):
    """
    Detects if the user's input expresses intent to generate an image.
    Returns: (is_image_intent: bool, extracted_prompt: str)
    """
    if not text:
        return False, ''
    t = text.strip()

    # 1. Slash commands: /image, /imagine, /generate, /draw, /art
    m = re.match(r'^/(?:image|imagine|generate|draw|art)\s+(.+)$', t, re.IGNORECASE | re.DOTALL)
    if m:
        return True, m.group(1).strip()

    # 2. Explicit generation intent patterns
    patterns = [
        r'^(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:generate|create|make|draw|paint|render|illustrate|produce)\s+(?:an?|me\s+an?|the)?\s*(?:image|picture|photo|photograph|portrait|drawing|sketch|artwork|illustration|wallpaper|visual)\s+(?:of\s+)?(.+)$',
        r'^(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:show|give)\s+(?:me\s+)?(?:an?|the)?\s*(?:image|picture|photo|drawing|illustration|visual)\s+of\s+(.+)$',
        r'^(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:draw|paint|illustrate)\s+(?:me\s+)?(?:an?|the)?\s*(.+)$',
        r'^(?:image|picture|photo|drawing|illustration)\s+of\s+(.+)$',
    ]
    for p in patterns:
        m = re.match(p, t, re.IGNORECASE | re.DOTALL)
        if m:
            clean = m.group(1).strip()
            clean = re.sub(r'[\.!\?]+$', '', clean).strip()
            return True, clean

    # 3. Substring matching for strong intent
    gen_kw = re.search(r'\b(generate|create|draw|paint)\s+(?:an?\s+)?(image|picture|photo|drawing)\s+of\s+(.+)', t, re.IGNORECASE)
    if gen_kw:
        clean = gen_kw.group(3).strip()
        clean = re.sub(r'[\.!\?]+$', '', clean).strip()
        return True, clean

    return False, ''


def enhance_prompt(prompt, style='photorealistic'):
    """Enriches a base prompt with artistic keywords tailored to pet care & aesthetics."""
    style_key = (style or 'photorealistic').lower()
    style_info = IMAGE_STYLES.get(style_key, IMAGE_STYLES['photorealistic'])
    suffix = style_info['suffix']

    clean_prompt = prompt.strip().rstrip('.,!?')
    # If the user prompt doesn't mention animal/pet context, keep it intact
    return f"{clean_prompt}, {suffix}"


def try_google_imagen(enhanced_prompt, api_key):
    """Attempt generation via Google Generative Language Imagen 3 REST endpoint."""
    if not api_key:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={api_key}"
        payload = {
            "instances": [{"prompt": enhanced_prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "1:1",
                "outputMimeType": "image/png"
            }
        }
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            predictions = data.get('predictions', [])
            if predictions and 'bytesBase64Encoded' in predictions[0]:
                b64 = predictions[0]['bytesBase64Encoded']
                return base64.b64decode(b64)
    except Exception as e:
        print(f"[Imagen 3] Skipped or failed: {e}")
    return None


def try_pollinations_diffusion(enhanced_prompt):
    """Attempt generation via Pollinations.ai free high-resolution diffusion engine."""
    try:
        encoded = urllib.parse.quote(enhanced_prompt)
        seed = random.randint(100000, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}&enhance=true&model=flux"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) PawSenseAI/2.0'
        }
        resp = requests.get(url, headers=headers, timeout=25)
        if resp.status_code == 200 and len(resp.content) > 5000:
            # Validate image bytes
            try:
                im = Image.open(BytesIO(resp.content))
                im.verify()
                return resp.content
            except Exception:
                pass
    except Exception as e:
        print(f"[Pollinations] Network error: {e}")
    return None


def draw_procedural_pet_image(prompt, style='photorealistic'):
    """
    Offline fallback: Generates an elegant, high-resolution procedural pet artwork
    using Pillow so the user ALWAYS receives a rendered image, even without an internet connection.
    """
    w, h = 800, 800
    
    # Palette selection based on style
    palettes = {
        'photorealistic': ((24, 28, 36), (45, 55, 72), (245, 158, 11), (255, 255, 255)),
        'cute-3d': ((255, 241, 242), (253, 164, 175), (244, 63, 94), (136, 19, 55)),
        'watercolor': ((240, 249, 255), (186, 230, 253), (14, 165, 233), (3, 105, 161)),
        'clinical': ((248, 250, 252), (226, 232, 240), (16, 185, 129), (15, 23, 42)),
        'fantasy': ((15, 23, 42), (59, 7, 100), (168, 85, 247), (250, 204, 21))
    }
    bg_dark, bg_mid, accent, text_color = palettes.get(style.lower(), palettes['photorealistic'])

    im = Image.new('RGB', (w, h), bg_dark)
    draw = ImageDraw.Draw(im)

    # 1. Background gradient simulation
    for y in range(h):
        r = int(bg_dark[0] + (bg_mid[0] - bg_dark[0]) * (y / h))
        g = int(bg_dark[1] + (bg_mid[1] - bg_dark[1]) * (y / h))
        b = int(bg_dark[2] + (bg_mid[2] - bg_dark[2]) * (y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # 2. Glowing central badge circle
    center_x, center_y = w // 2, h // 2 - 40
    radii = [240, 220, 200, 180]
    opacities = [0.15, 0.25, 0.4, 0.9]
    for r, op in zip(radii, opacities):
        ring_col = (
            int(accent[0] * op + bg_mid[0] * (1 - op)),
            int(accent[1] * op + bg_mid[1] * (1 - op)),
            int(accent[2] * op + bg_mid[2] * (1 - op)),
        )
        draw.ellipse([center_x - r, center_y - r, center_x + r, center_y + r], fill=ring_col)

    # 3. Draw Stylized Paw Print in Center
    paw_col = accent
    paw_pad_r = 50
    # Main pad (inverted heart/rounded bean)
    draw.ellipse([center_x - 55, center_y - 20, center_x + 55, center_y + 70], fill=paw_col)
    draw.ellipse([center_x - 40, center_y + 10, center_x + 40, center_y + 80], fill=paw_col)
    
    # 4 Toe beans
    toe_offsets = [(-60, -50), (-22, -80), (22, -80), (60, -50)]
    for ox, oy in toe_offsets:
        draw.ellipse([center_x + ox - 20, center_y + oy - 25, center_x + ox + 20, center_y + oy + 25], fill=paw_col)

    # 4. Floating decorative mini-stars / paw prints
    random.seed(abs(hash(prompt)))
    for _ in range(12):
        sx = random.randint(40, w - 40)
        sy = random.randint(40, h - 180)
        if abs(sx - center_x) > 180 or abs(sy - center_y) > 180:
            s_rad = random.randint(4, 9)
            draw.ellipse([sx - s_rad, sy - s_rad, sx + s_rad, sy + s_rad], fill=accent)

    # 5. Bottom Banner Card
    card_h = 170
    draw.rectangle([30, h - card_h - 30, w - 30, h - 30], fill=(255, 255, 255, 240) if bg_dark[0] > 100 else (20, 25, 35))

    # Border
    draw.rectangle([30, h - card_h - 30, w - 30, h - 30], outline=accent, width=2)

    # Header pill
    style_label = IMAGE_STYLES.get(style.lower(), {}).get('name', 'PawSense AI')
    draw.rectangle([50, h - card_h - 18, 260, h - card_h + 10], fill=accent)
    draw.text((60, h - card_h - 15), f"✨ AI PET STUDIO • {style_label.upper()}", fill=(255, 255, 255))

    # Text wrapping for prompt
    words = prompt.split()
    lines = []
    curr = []
    for word in words:
        curr.append(word)
        if len(' '.join(curr)) > 42:
            lines.append(' '.join(curr[:-1]))
            curr = [word]
    if curr:
        lines.append(' '.join(curr))

    y_text = h - card_h + 22
    for line in lines[:2]:
        draw.text((50, y_text), line, fill=text_color)
        y_text += 26

    draw.text((50, h - 55), "🐾 Generated by PawSense AI Intelligent Petcare Companion", fill=(140, 150, 165))

    buf = BytesIO()
    im.save(buf, format='PNG', quality=95)
    return buf.getvalue()


def generate_pet_image(prompt, style='photorealistic', upload_folder=None, api_key=None):
    """
    Master generation pipeline:
    1. Enhances prompt
    2. Tries Google Imagen 3 (Tier 1)
    3. Tries Pollinations Diffusion (Tier 2)
    4. Falls back to Procedural Pillow Pet Graphic (Tier 3)
    5. Saves full PNG & WebP thumbnail to upload_folder
    Returns dictionary with image metadata and web-accessible paths.
    """
    enhanced = enhance_prompt(prompt, style=style)
    engine_used = 'procedural'
    img_bytes = None

    # Tier 1: Google Imagen 3
    if api_key:
        img_bytes = try_google_imagen(enhanced, api_key)
        if img_bytes:
            engine_used = 'google-imagen-3'

    # Tier 2: Pollinations.ai Flux Diffusion
    if not img_bytes:
        img_bytes = try_pollinations_diffusion(enhanced)
        if img_bytes:
            engine_used = 'pollinations-flux'

    # Tier 3: High-Fidelity Procedural Fallback
    if not img_bytes:
        img_bytes = draw_procedural_pet_image(prompt, style=style)
        engine_used = 'procedural-art'

    # Generate unique filenames
    token = secrets.token_hex(8)
    png_filename = f"generated_{token}.png"
    thumb_filename = f"generated_{token}_thumb.webp"

    # Default to static/uploads if no folder passed
    if not upload_folder:
        upload_folder = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
    os.makedirs(upload_folder, exist_ok=True)

    png_path = os.path.join(upload_folder, png_filename)
    thumb_path = os.path.join(upload_folder, thumb_filename)

    # Save original PNG
    with open(png_path, 'wb') as f:
        f.write(img_bytes)

    # Create optimized thumbnail
    try:
        im = Image.open(BytesIO(img_bytes))
        im.thumbnail((480, 480))
        im.save(thumb_path, format='WEBP', quality=80)
    except Exception as e:
        print(f"Thumbnail generation warning: {e}")
        thumb_filename = png_filename

    return {
        'success': True,
        'filename': png_filename,
        'thumb_filename': thumb_filename,
        'image_path': f"protected_uploads/{png_filename}",
        'thumb_path': f"protected_uploads/{thumb_filename}",
        'static_url': f"static/uploads/{png_filename}",
        'prompt': prompt,
        'enhanced_prompt': enhanced,
        'style': style,
        'engine': engine_used
    }
