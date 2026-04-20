"""
generate_favicon.py
로고 이미지에서 파비콘/아이콘 자동 생성

사용법: python generate_favicon.py [로고이미지경로]
"""
import sys
import os
from PIL import Image

def generate(src_path):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)

    img = Image.open(src_path).convert("RGBA")
    print(f"원본: {img.size}")

    # logo.png (512x512)
    logo = img.copy()
    logo.thumbnail((512, 512), Image.LANCZOS)
    logo.save(os.path.join(static_dir, "logo.png"), "PNG")
    print("✅ logo.png (512x512)")

    # og_image.png (1200x630) - 배경 + 로고 중앙
    og = Image.new("RGBA", (1200, 630), (15, 17, 23, 255))
    thumb = img.copy()
    thumb.thumbnail((400, 400), Image.LANCZOS)
    x = (1200 - thumb.width) // 2
    y = (630 - thumb.height) // 2
    og.paste(thumb, (x, y), thumb)
    og.convert("RGB").save(os.path.join(static_dir, "og_image.png"), "PNG")
    print("✅ og_image.png (1200x630)")

    # apple-touch-icon.png (180x180)
    apple = img.copy()
    apple.thumbnail((180, 180), Image.LANCZOS)
    apple_bg = Image.new("RGBA", (180, 180), (15, 17, 23, 255))
    ax = (180 - apple.width) // 2
    ay = (180 - apple.height) // 2
    apple_bg.paste(apple, (ax, ay), apple)
    apple_bg.convert("RGB").save(os.path.join(static_dir, "apple-touch-icon.png"), "PNG")
    print("✅ apple-touch-icon.png (180x180)")

    # favicon-32x32.png
    f32 = img.copy()
    f32.thumbnail((32, 32), Image.LANCZOS)
    f32.save(os.path.join(static_dir, "favicon-32x32.png"), "PNG")
    print("✅ favicon-32x32.png")

    # favicon-16x16.png
    f16 = img.copy()
    f16.thumbnail((16, 16), Image.LANCZOS)
    f16.save(os.path.join(static_dir, "favicon-16x16.png"), "PNG")
    print("✅ favicon-16x16.png")

    # favicon.ico (멀티사이즈)
    ico16 = img.copy()
    ico16.thumbnail((16, 16), Image.LANCZOS)
    ico32 = img.copy()
    ico32.thumbnail((32, 32), Image.LANCZOS)
    ico48 = img.copy()
    ico48.thumbnail((48, 48), Image.LANCZOS)
    ico16.save(os.path.join(static_dir, "favicon.ico"), format="ICO", sizes=[(16,16), (32,32), (48,48)])
    print("✅ favicon.ico")

    print(f"\n모든 파일이 {static_dir}/ 에 생성되었습니다.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python generate_favicon.py [로고이미지경로]")
        print("예: python generate_favicon.py ~/Downloads/logo.png")
    else:
        generate(sys.argv[1])
