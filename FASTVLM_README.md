# Apple FastVLM-7B Installation & Test Guide

Apple's FastVLM is a fast and efficient Vision Language Model from CVPR 2025.

## Quick Installation

```bash
# Install dependencies
pip install torch torchvision transformers pillow accelerate

# Or use uv
uv pip install torch torchvision transformers pillow accelerate
```

## Usage

```bash
# Run with an image
python test_fastvlm.py test.png

# Run with custom prompt
python test_fastvlm.py photo.jpg "What objects are visible in this image?"
```

## Model Details

- **Model**: Apple FastVLM-1.5B (2B params)
- **Size**: ~3GB download
- **Architecture**: Qwen2 LLM + FastViTHD vision encoder
- **Performance**: 85x faster TTFT than LLaVA-OneVision-0.5B
- **Supports**: High-resolution images

## Example Output

```python
Input: "Describe this image"
Output: "The image shows a modern office space with..."
```

## Supported Devices

- ✅ **Mac (Apple Silicon)**: Uses MPS acceleration
- ✅ **CUDA GPUs**: Automatic FP16 optimization
- ✅ **CPU**: Falls back to FP32 (slower)

## First Run

The first run will download the model (~3GB). Subsequent runs are much faster.

## Troubleshooting

**"trust_remote_code" error**:
- Make sure you have transformers >= 4.35.0

**Out of memory**:
- Try the smaller model: `apple/FastVLM-0.5B` (~1GB)
- Note: int4 quantized models require MLX framework, not transformers

## Available Models

- **FastVLM-0.5B**: ~1GB, fastest/smallest
- **FastVLM-1.5B**: ~3GB, balanced performance (recommended)
- **FastVLM-7B**: ~14GB, highest quality
- **int4/int8 variants**: Require MLX framework (Mac only)

## Sources

- [GitHub Repository](https://github.com/apple/ml-fastvlm)
- [Hugging Face Model](https://huggingface.co/apple/FastVLM-7B)
- [Research Paper](https://machinelearning.apple.com/research/fast-vision-language-models)
- [Model Info](https://www.ultralytics.com/blog/fastvlm-apple-introduces-its-new-fast-vision-language-model)
