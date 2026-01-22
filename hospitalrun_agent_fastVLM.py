#!/usr/bin/env python3
"""
Computer Use Agent using Apple FastVLM-1.5B + OCR
"""

import os
import time
import json
import re
from typing import List, Dict, Any
from io import BytesIO

import torch
from PIL import Image
from transformers import AutoTokenizer, AutoModelForCausalLM
import pyautogui
import pytesseract


class ComputerUseTools:
    """Computer interaction tools with OCR"""

    def __init__(self):
        self.display_width, self.display_height = pyautogui.size()
        print(f"Display: {self.display_width}x{self.display_height}")
        pyautogui.FAILSAFE = True

    def extract_ocr_data(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Extract text with bounding boxes using Tesseract"""
        try:
            ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            words = []

            for i in range(len(ocr_data['text'])):
                conf = int(ocr_data['conf'][i])
                text = ocr_data['text'][i].strip()

                if conf < 30 or not text:
                    continue

                x, y = ocr_data['left'][i], ocr_data['top'][i]
                w, h = ocr_data['width'][i], ocr_data['height'][i]

                words.append({
                    'text': text,
                    'x': x, 'y': y,
                    'width': w, 'height': h,
                    'center_x': x + w // 2,
                    'center_y': y + h // 2,
                    'confidence': conf
                })

            return words
        except Exception as e:
            print(f"OCR error: {e}")
            return []

    def take_screenshot(self) -> tuple[Image.Image, List[Dict[str, Any]]]:
        """Take screenshot and extract OCR data"""
        screenshot = pyautogui.screenshot()

        if screenshot.size != (self.display_width, self.display_height):
            screenshot = screenshot.resize(
                (self.display_width, self.display_height),
                Image.Resampling.LANCZOS
            )

        ocr_words = self.extract_ocr_data(screenshot)
        print(f"OCR: {len(ocr_words)} words detected")

        return screenshot, ocr_words

    def execute_action(self, action: Dict[str, Any]) -> str:
        """Execute computer action"""
        action_type = action.get("type")

        try:
            if action_type == "click":
                x, y = action.get("x"), action.get("y")
                button = action.get("button", "left")

                if x is not None and y is not None:
                    pyautogui.click(x, y, button=button)
                    print(f"✓ Click ({x}, {y})")
                    time.sleep(0.5)
                    return "success"

            elif action_type == "type":
                text = action.get("text", "")
                pyautogui.write(text, interval=0.05)
                print(f"✓ Type: {text}")
                time.sleep(0.2)
                return "success"

            elif action_type == "keypress":
                keys = action.get("keys", [])
                mapped = [self._map_key(k) for k in keys]

                if len(mapped) > 1:
                    time.sleep(0.5)
                    pyautogui.hotkey(*mapped)
                    time.sleep(0.5)
                else:
                    pyautogui.press(mapped[0])

                print(f"✓ Keypress: {'+'.join(mapped)}")
                return "success"

            elif action_type == "scroll":
                amount = action.get("amount", 0)
                pyautogui.scroll(amount)
                print(f"✓ Scroll: {amount}")
                time.sleep(0.3)
                return "success"

            elif action_type == "wait":
                duration = action.get("duration", 1)
                time.sleep(duration)
                print(f"✓ Wait: {duration}s")
                return "success"

            return f"Unknown action type: {action_type}"

        except Exception as e:
            return f"Error: {str(e)}"

    def _map_key(self, key_name: str) -> str:
        """Map key names to pyautogui format"""
        key_map = {
            "cmd": "command", "ctrl": "ctrl", "alt": "option",
            "shift": "shift", "enter": "enter", "return": "enter",
            "tab": "tab", "space": "space", "backspace": "backspace",
            "delete": "delete", "esc": "escape",
            "arrowup": "up", "arrowdown": "down",
            "arrowleft": "left", "arrowright": "right",
        }
        return key_map.get(key_name.lower(), key_name)


class FastVLMAgent:
    """Computer use agent powered by FastVLM-1.5B"""

    def __init__(self, model_id: str = "apple/FastVLM-1.5B"):
        print(f"\nLoading FastVLM model: {model_id}...")

        self.computer_tools = ComputerUseTools()
        self.IMAGE_TOKEN_INDEX = -200

        # Detect device
        if torch.cuda.is_available():
            self.device = "cuda"
            self.dtype = torch.float16
            device_map = "auto"
        elif torch.backends.mps.is_available():
            self.device = "mps"
            self.dtype = torch.float32
            device_map = None
        else:
            self.device = "cpu"
            self.dtype = torch.float32
            device_map = None

        print(f"Using device: {self.device}")

        # Load model
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

        if device_map:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=self.dtype,
                device_map=device_map,
                trust_remote_code=True,
            )
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=self.dtype,
                trust_remote_code=True,
            )
            self.model = self.model.to(self.device)

        print("✓ Model loaded\n")

    def _format_ocr_data(self, ocr_words: List[Dict[str, Any]], max_lines: int = 30) -> str:
        """Format OCR data for prompt (limit output to avoid context overflow)"""
        if not ocr_words:
            return "OCR: No text detected"

        # Group by line
        grouped = []
        current_line = []
        last_y = None

        for word in ocr_words:
            if last_y is not None and abs(word['y'] - last_y) > 20:
                if current_line:
                    grouped.append(current_line)
                current_line = []
            current_line.append(word)
            last_y = word['y']

        if current_line:
            grouped.append(current_line)

        # Format output (limit lines to keep prompt concise)
        output = "SCREEN TEXT:\n"
        for i, line in enumerate(grouped[:max_lines]):
            text = ' '.join([w['text'] for w in line])
            avg_x = sum(w['center_x'] for w in line) // len(line)
            avg_y = sum(w['center_y'] for w in line) // len(line)
            output += f"'{text}' ({avg_x},{avg_y})\n"

        if len(grouped) > max_lines:
            output += f"... +{len(grouped) - max_lines} more lines\n"

        return output

    def _create_prompt(self, task: str, ocr_text: str, iteration: int = 0) -> str:
        """Create instruction prompt"""
        w, h = self.computer_tools.display_width, self.computer_tools.display_height

        if iteration == 0:
            return f"""You are controlling a computer. Your goal: {task}

Current screen shows ({w}x{h} pixels):
{ocr_text}

CRITICAL RULES:
1. Analyze the OCR text to understand what's visible on screen
2. Decide ONE action to take toward the goal
3. Respond with ONLY a valid JSON action, nothing else

ACTION EXAMPLES:
- Click a button: {{"type": "click", "x": 500, "y": 300}}
- Type text: {{"type": "type", "text": "John Doe"}}
- Press keys: {{"type": "keypress", "keys": ["cmd", "a"]}}
- Scroll: {{"type": "scroll", "amount": -5}}
- Task done: {{"type": "complete"}}

STRATEGY:
- Look for buttons/links in OCR text and click their coordinates
- To fill forms: click field location, then type text
- Input fields are typically BELOW their label text (add 30-40px to Y)
- Use exact OCR coordinates for clicking visible text

RESPOND WITH ONLY JSON - NO EXPLANATIONS:"""
        else:
            return f"""Previous action completed.

Current screen:
{ocr_text}

Goal: {task}

Next action (JSON only):"""

    def _query_model(self, screenshot: Image.Image, prompt: str) -> str:
        """Query FastVLM with image and prompt"""
        # Build chat messages
        messages = [{"role": "user", "content": f"<image>\n{prompt}"}]

        # Tokenize with image placeholder
        rendered = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        pre, post = rendered.split("<image>", 1)

        pre_ids = self.tokenizer(pre, return_tensors="pt", add_special_tokens=False).input_ids
        post_ids = self.tokenizer(post, return_tensors="pt", add_special_tokens=False).input_ids
        img_tok = torch.tensor([[self.IMAGE_TOKEN_INDEX]], dtype=pre_ids.dtype)

        input_ids = torch.cat([pre_ids, img_tok, post_ids], dim=1).to(self.device)
        attention_mask = torch.ones_like(input_ids, device=self.device)

        # Process image
        px = self.model.get_vision_tower().image_processor(
            images=screenshot, return_tensors="pt"
        )["pixel_values"]
        px = px.to(self.device, dtype=self.dtype)

        # Generate with higher temperature for more diverse responses
        with torch.no_grad():
            out = self.model.generate(
                inputs=input_ids,
                attention_mask=attention_mask,
                images=px,
                max_new_tokens=150,
                do_sample=False,
                temperature=None,
            )

        response = self.tokenizer.decode(out[0], skip_special_tokens=True)

        # Extract only new content (remove the prompt echo)
        # The model often repeats the input, so extract only what's after
        markers = ["RESPOND WITH ONLY JSON - NO EXPLANATIONS:", "Next action (JSON only):"]
        for marker in markers:
            if marker in response:
                response = response.split(marker)[-1].strip()
                break

        # Also try to extract content after common assistant markers
        if "assistant" in response.lower():
            parts = response.lower().split("assistant")
            if len(parts) > 1:
                response = response[response.lower().rfind("assistant")+9:].strip()

        return response

    def _parse_action(self, response: str) -> Dict[str, Any]:
        """Parse action from model response"""
        # Check for completion keywords
        completion_keywords = ["TASK_COMPLETE", "complete", "finished", "done"]
        if any(kw in response.lower() for kw in completion_keywords):
            # But make sure it's not just in a sentence, check for explicit completion
            if '"type": "complete"' in response or response.strip().lower() in completion_keywords:
                return {"type": "complete"}

        # Try to find JSON with flexible matching
        # Match nested braces too
        json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}'
        json_matches = re.findall(json_pattern, response)

        for json_str in json_matches:
            try:
                action = json.loads(json_str)
                # Validate it has required 'type' field
                if isinstance(action, dict) and 'type' in action:
                    return action
            except json.JSONDecodeError:
                continue

        # If no valid JSON found, try to extract action from natural language
        # Look for action keywords
        response_lower = response.lower()

        # Try to find click coordinates
        click_match = re.search(r'click.*?(\d+)[,\s]+(\d+)', response_lower)
        if click_match:
            return {"type": "click", "x": int(click_match.group(1)), "y": int(click_match.group(2))}

        # Try to find type action
        type_match = re.search(r'type[:\s]+"([^"]+)"', response_lower)
        if type_match:
            return {"type": "type", "text": type_match.group(1)}

        print(f"⚠️  Could not parse action from: {response[:100]}")
        return None

    def process_task(self, task: str, max_iterations: int = 20) -> List[Dict[str, Any]]:
        """Process task using vision + OCR feedback loop"""
        print(f"\n{'='*60}")
        print(f"TASK: {task}")
        print(f"{'='*60}\n")

        actions_log = []

        for iteration in range(max_iterations):
            print(f"\n--- Iteration {iteration + 1} ---")

            # Take screenshot + OCR
            screenshot, ocr_words = self.computer_tools.take_screenshot()
            ocr_text = self._format_ocr_data(ocr_words)

            # Create prompt
            prompt = self._create_prompt(task, ocr_text, iteration)

            # Query model
            print("Querying FastVLM...")
            response = self._query_model(screenshot, prompt)

            # Show full response for debugging
            print(f"\n--- RAW MODEL OUTPUT ---")
            print(response[:500])
            print(f"--- END OUTPUT ---\n")

            # Parse action
            action = self._parse_action(response)

            if action:
                print(f"✓ Parsed action: {action}")

            if action is None:
                print("⚠️  Could not parse action, continuing...")
                time.sleep(1)
                continue

            if action.get("type") == "complete":
                print("✓ Task complete")
                break

            # Execute action
            print(f"Executing: {action}")
            result = self.computer_tools.execute_action(action)

            actions_log.append({
                "action": action,
                "result": result,
                "iteration": iteration + 1
            })

            time.sleep(1)

        print(f"\n{'='*60}")
        print(f"Completed {len(actions_log)} actions")
        print(f"{'='*60}\n")

        return actions_log


def main():
    """Test the agent"""
    agent = FastVLMAgent()

    # Example task
    task = input("Enter task: ").strip()
    if not task:
        task = "Click on the Chrome icon"

    actions = agent.process_task(task)

    print("\nActions taken:")
    for i, action in enumerate(actions, 1):
        print(f"{i}. {action['action']} → {action['result']}")


if __name__ == "__main__":
    main()
