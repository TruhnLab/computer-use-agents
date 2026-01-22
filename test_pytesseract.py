#!/usr/bin/env python3
"""
Test pytesseract OCR on an image and create an overlay with bounding boxes
"""

import sys
from pathlib import Path
try:
    from PIL import Image, ImageDraw, ImageFont
    import pytesseract
except ImportError as e:
    print(f"Error: Missing required package - {e}")
    print("\nInstall with:")
    print("  pip install pillow pytesseract")
    print("\nAlso ensure Tesseract OCR is installed:")
    print("  brew install tesseract")
    sys.exit(1)

def perform_ocr_with_overlay(image_path: str, output_path: str = "test_overlay.png"):
    """
    Perform OCR on an image and create an overlay with detected text and bounding boxes

    Args:
        image_path: Path to input image
        output_path: Path to save the overlay image
    """
    print("="*80)
    print("PYTESSERACT OCR TEST")
    print("="*80)

    # Load image
    image_path = Path(image_path)
    if not image_path.exists():
        print(f"\n❌ Error: Image not found at {image_path}")
        print("\nPlease provide a valid image path.")
        return False

    print(f"\n📄 Loading image: {image_path}")
    try:
        image = Image.open(image_path)
    except Exception as e:
        print(f"❌ Error loading image: {e}")
        return False

    print(f"   Size: {image.size}")
    print(f"   Mode: {image.mode}")

    # Convert to RGB if needed
    if image.mode != 'RGB':
        print(f"   Converting from {image.mode} to RGB...")
        image = image.convert('RGB')

    # Perform OCR with detailed data
    print("\n🔍 Performing OCR with pytesseract...")
    try:
        # Get detailed OCR data including bounding boxes
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        # Also get just the text for display
        text = pytesseract.image_to_string(image)

    except Exception as e:
        print(f"❌ Error during OCR: {e}")
        print("\nMake sure Tesseract is installed:")
        print("  brew install tesseract")
        return False

    # Print detected text
    print("\n📝 Detected Text:")
    print("-" * 80)
    print(text)
    print("-" * 80)

    # Create overlay image
    print("\n🎨 Creating overlay image...")
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)

    # Load font
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except:
        font = ImageFont.load_default()

    # Process OCR data
    n_boxes = len(ocr_data['text'])
    detected_words = 0
    word_data = []

    print(f"\n📊 Processing {n_boxes} detected regions...")

    for i in range(n_boxes):
        # Get confidence and text
        conf = int(ocr_data['conf'][i])
        text = ocr_data['text'][i].strip()

        # Skip empty text or low confidence
        if conf < 0 or not text:
            continue

        detected_words += 1

        # Get bounding box
        x = ocr_data['left'][i]
        y = ocr_data['top'][i]
        w = ocr_data['width'][i]
        h = ocr_data['height'][i]

        # Calculate center point
        center_x = x + w // 2
        center_y = y + h // 2

        # Choose color based on confidence
        if conf >= 80:
            color = (0, 255, 0)  # Green - high confidence
            color_name = "GREEN"
        elif conf >= 60:
            color = (255, 165, 0)  # Orange - medium confidence
            color_name = "ORANGE"
        else:
            color = (255, 0, 0)  # Red - low confidence
            color_name = "RED"

        # Store word data
        word_data.append({
            'text': text,
            'x': x,
            'y': y,
            'width': w,
            'height': h,
            'center_x': center_x,
            'center_y': center_y,
            'confidence': conf,
            'color': color_name
        })

        # Draw bounding box
        draw.rectangle(
            [(x, y), (x + w, y + h)],
            outline=color,
            width=2
        )

        # Draw text label with confidence
        label = f"{text} ({conf}%)"

        # Draw background for text label
        bbox = draw.textbbox((x, y - 15), label, font=font)
        draw.rectangle(bbox, fill=color)

        # Draw text
        draw.text((x, y - 15), label, fill=(0, 0, 0), font=font)

    # Save overlay image
    output_path = Path(output_path)
    print(f"\n💾 Saving overlay image to: {output_path}")
    overlay.save(output_path)

    # Print detailed word data
    print("\n" + "="*80)
    print("DETECTED WORDS AND COORDINATES")
    print("="*80)
    print(f"{'#':<4} {'Word':<20} {'X':<6} {'Y':<6} {'W':<6} {'H':<6} {'Center':<12} {'Conf':<6} {'Color':<8}")
    print("-" * 80)

    for i, word in enumerate(word_data, 1):
        center_str = f"({word['center_x']},{word['center_y']})"
        print(f"{i:<4} {word['text']:<20} {word['x']:<6} {word['y']:<6} {word['width']:<6} {word['height']:<6} {center_str:<12} {word['confidence']:<6}% {word['color']:<8}")

    # Print summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"✓ Words detected: {detected_words}")
    print(f"✓ Overlay saved: {output_path}")

    print("\nColor coding:")
    print("  🟢 Green boxes: High confidence (≥80%)")
    print("  🟠 Orange boxes: Medium confidence (60-79%)")
    print("  🔴 Red boxes: Low confidence (<60%)")

    print("\nCoordinates explained:")
    print("  X, Y: Top-left corner of bounding box")
    print("  W, H: Width and height of bounding box")
    print("  Center: Center point (useful for clicking)")
    print("\nTo click on a word, use its Center coordinates!")

    # Try to open the image
    try:
        import subprocess
        import platform
        if platform.system() == "Darwin":  # macOS
            subprocess.run(["open", str(output_path)], check=False)
            print(f"\n👁️  Opening overlay image...")
    except:
        pass

    return True

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Perform OCR on an image and create an overlay with bounding boxes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_pytesseract.py test.png
  python test_pytesseract.py screenshot.png -o result.png

Color coding:
  Green:  High confidence (≥80%)
  Orange: Medium confidence (60-79%)
  Red:    Low confidence (<60%)
        """
    )

    parser.add_argument(
        'image',
        nargs='?',
        default='test.png',
        help='Path to input image (default: test.png)'
    )

    parser.add_argument(
        '-o', '--output',
        default='test_overlay.png',
        help='Path to save overlay image (default: test_overlay.png)'
    )

    args = parser.parse_args()

    # Perform OCR
    success = perform_ocr_with_overlay(args.image, args.output)

    if success:
        print("\n✅ OCR completed successfully!")
    else:
        print("\n❌ OCR failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
