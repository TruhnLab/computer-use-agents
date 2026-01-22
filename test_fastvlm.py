#!/usr/bin/env python3
"""
Test script for Apple's FastVLM-7B Vision Language Model
Official model: https://huggingface.co/apple/FastVLM-7B
"""

import torch
from PIL import Image
from transformers import AutoTokenizer, AutoModelForCausalLM
import sys

print("=" * 70)
print("Apple FastVLM-7B Test Script")
print("=" * 70)

# Configuration - using 1.5B model (2B params, ~3GB)
MID = "apple/FastVLM-1.5B"  # Mid-size model (~3GB) - works with transformers
# Note: int4 models are for MLX framework, not transformers
IMAGE_TOKEN_INDEX = -200  # Special token for image placeholder

def test_fastvlm(image_path: str, prompt: str = "Describe this image in detail."):
    """Test FastVLM-7B with an image and prompt"""

    print(f"\n1. Loading tokenizer and model from: {MID}")
    if "int4" in MID:
        print("   (Using 4-bit quantized model - first run will download ~4GB)")
    elif "0.5B" in MID:
        print("   (Using 0.5B small model - first run will download ~1GB)")
    elif "1.5B" in MID:
        print("   (Using 1.5B mid-size model - first run will download ~3GB)")
    else:
        print("   (First run will download ~14GB model)")

    # Determine device and dtype
    if torch.cuda.is_available():
        device = "cuda"
        dtype = torch.float16
        device_map = "auto"
    elif torch.backends.mps.is_available():
        device = "mps"
        dtype = torch.float32  # MPS works better with float32
        device_map = None  # Don't use device_map on Mac
    else:
        device = "cpu"
        dtype = torch.float32
        device_map = None

    print(f"   Loading on: {device} with {dtype}")

    # Load tokenizer and model
    tok = AutoTokenizer.from_pretrained(MID, trust_remote_code=True)

    if device_map:
        # CUDA with device_map
        model = AutoModelForCausalLM.from_pretrained(
            MID,
            torch_dtype=dtype,
            device_map=device_map,
            trust_remote_code=True,
        )
    else:
        # MPS or CPU - load without device_map
        model = AutoModelForCausalLM.from_pretrained(
            MID,
            torch_dtype=dtype,
            trust_remote_code=True,
        )
        model = model.to(device)

    print(f"   ✓ Model loaded on: {device}")

    # Build chat messages
    print(f"\n2. Preparing prompt: '{prompt}'")
    messages = [
        {"role": "user", "content": f"<image>\n{prompt}"}
    ]

    # Render chat template and split around image token
    rendered = tok.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False
    )
    pre, post = rendered.split("<image>", 1)

    # Tokenize text before and after image placeholder
    pre_ids = tok(pre, return_tensors="pt", add_special_tokens=False).input_ids
    post_ids = tok(post, return_tensors="pt", add_special_tokens=False).input_ids

    # Insert IMAGE_TOKEN_INDEX at placeholder position
    img_tok = torch.tensor([[IMAGE_TOKEN_INDEX]], dtype=pre_ids.dtype)
    input_ids = torch.cat([pre_ids, img_tok, post_ids], dim=1).to(device)
    attention_mask = torch.ones_like(input_ids, device=device)

    # Load and preprocess image
    print(f"\n3. Loading image: {image_path}")
    try:
        img = Image.open(image_path).convert("RGB")
        print(f"   ✓ Image size: {img.size}")
    except FileNotFoundError:
        print(f"   ✗ Error: Image file not found: {image_path}")
        return

    px = model.get_vision_tower().image_processor(images=img, return_tensors="pt")["pixel_values"]
    px = px.to(device, dtype=dtype)

    # Generate response
    print("\n4. Generating response...")
    with torch.no_grad():
        out = model.generate(
            inputs=input_ids,
            attention_mask=attention_mask,
            images=px,
            max_new_tokens=256,
            do_sample=False,
        )

    # Decode and print response
    response = tok.decode(out[0], skip_special_tokens=True)

    print("\n" + "=" * 70)
    print("MODEL RESPONSE:")
    print("=" * 70)
    print(response)
    print("=" * 70)

    return response


def main():
    """Main test function"""
    print("\nThis script tests Apple's FastVLM-7B vision-language model.")
    print("The model can analyze images and answer questions about them.\n")

    # Check if image path is provided
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        prompt = sys.argv[2] if len(sys.argv) > 2 else "Describe this image in detail."
    else:
        print("Usage: python test_fastvlm.py <image_path> [prompt]")
        print("\nExample:")
        print("  python test_fastvlm.py test.png 'What objects are in this image?'")
        print("  python test_fastvlm.py photo.jpg")
        return

    # Run test
    test_fastvlm(image_path, prompt)


if __name__ == "__main__":
    main()
