#!/usr/bin/env python3
"""
HospitalRun Navigation Agent using Azure OpenAI Computer Use
"""

import os
import base64
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from io import BytesIO

from openai import OpenAI
from dotenv import load_dotenv
import pyautogui
from PIL import Image
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Users\DEschweiler\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'

# Load environment variables
load_dotenv()


class ComputerUseTools:
    """Tools for computer interaction: screenshot, keyboard, and mouse actions"""

    def __init__(self, display_width: int = None, display_height: int = None):
        # Use actual logical screen dimensions (not physical Retina pixels)
        actual_width, actual_height = pyautogui.size()

        # IMPORTANT: display_width/height MUST match screenshot dimensions sent to AI
        # For Mac Retina: Use logical screen size (1512x982), NOT physical (3024x1964)
        if display_width is None or display_height is None:
            self.display_width = actual_width
            self.display_height = actual_height
        else:
            self.display_width = display_width
            self.display_height = display_height

        print(f"Display dimensions: {self.display_width}x{self.display_height}")
        pyautogui.FAILSAFE = True  # Move mouse to corner to abort

    def scale_coordinates(self, x: int, y: int) -> tuple[int, int]:
        """
        No scaling needed - AI coordinates match screen coordinates directly.

        Per Azure OpenAI docs: screenshot dimensions MUST match display_width/height
        specified in tool config. When they match, AI returns coordinates in the
        same space as the screen, so we use them directly.
        """
        return int(x), int(y)

    def extract_ocr_data(self, image: Image.Image) -> List[Dict[str, Any]]:
        """
        Extract text and bounding boxes using Tesseract OCR

        Returns list of detected words with their coordinates:
        [
            {
                'text': 'Button',
                'x': 100,
                'y': 200,
                'width': 80,
                'height': 30,
                'center_x': 140,
                'center_y': 215,
                'confidence': 95
            },
            ...
        ]
        """
        try:
            # Get OCR data with bounding boxes
            ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

            # Process and filter OCR results
            words = []
            n_boxes = len(ocr_data['text'])

            for i in range(n_boxes):
                conf = int(ocr_data['conf'][i])
                text = ocr_data['text'][i].strip()

                # Skip empty text or very low confidence
                if conf < 30 or not text:
                    continue

                x = ocr_data['left'][i]
                y = ocr_data['top'][i]
                w = ocr_data['width'][i]
                h = ocr_data['height'][i]

                words.append({
                    'text': text,
                    'x': x,
                    'y': y,
                    'width': w,
                    'height': h,
                    'center_x': x + w // 2,
                    'center_y': y + h // 2,
                    'confidence': conf
                })

            return words

        except Exception as e:
            print(f"Warning: OCR failed: {e}")
            return []

    def take_screenshot(self) -> tuple[str, List[Dict[str, Any]]]:
        """
        Take a screenshot and extract OCR data

        Returns:
            tuple: (base64_screenshot, ocr_words_list)
        """
        import subprocess
        import tempfile
        import platform

        # Use native screenshot tools for better reliability
        if platform.system() == "Darwin":  # macOS
            # Use native screencapture command
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                subprocess.run(["screencapture", "-x", tmp_path], check=True)
                screenshot = Image.open(tmp_path)
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        else:
            # Fall back to pyautogui for other platforms
            screenshot = pyautogui.screenshot()

        # CRITICAL: Resize to match display_width/height (Azure OpenAI requirement)
        # Screenshot dimensions MUST match the display dimensions specified in tool config
        # For Mac Retina: Capture at 3024x1964, resize to 1512x982 (logical screen size)
        if screenshot.size != (self.display_width, self.display_height):
            screenshot = screenshot.resize(
                (self.display_width, self.display_height),
                Image.Resampling.LANCZOS  # High-quality downsampling
            )

        # Extract OCR data from the resized screenshot
        print("Running OCR on screenshot...")
        ocr_words = self.extract_ocr_data(screenshot)
        print(f"OCR detected {len(ocr_words)} words")

        # Convert to base64
        buffered = BytesIO()
        screenshot.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()

        return img_str, ocr_words

    def execute_action(self, action: Dict[str, Any]) -> str:
        """Execute a computer action based on the computer_use_preview tool format"""
        action_type = action.get("type")

        try:
            if action_type == "click":
                x = action.get("x")
                y = action.get("y")
                button = action.get("button", "left")

                if x is not None and y is not None:
                    if 0 <= x <= self.display_width and 0 <= y <= self.display_height:
                        pyautogui.click(x, y, button=button)
                        print(f"✓ Clicked at ({x}, {y}) with {button} button")
                        # Longer delay for Electron/desktop apps to register focus
                        time.sleep(0.5)
                        return "success"
                    else:
                        print(f"✗ Coordinates ({x}, {y}) out of bounds")
                        return "error: coordinates out of bounds"
                else:
                    pyautogui.click(button=button)
                    print(f"✓ Clicked at current position")
                    time.sleep(0.5)
                    return "success"

            elif action_type == "double_click":
                x = action.get("x")
                y = action.get("y")

                if x is not None and y is not None:
                    if 0 <= x <= self.display_width and 0 <= y <= self.display_height:
                        pyautogui.doubleClick(x, y)
                        print(f"✓ Double-clicked at ({x}, {y})")
                        return "success"
                    else:
                        print(f"✗ Coordinates ({x}, {y}) out of bounds")
                        return "error: coordinates out of bounds"

            elif action_type == "drag":
                from_x = action.get("from_x") or action.get("x")
                from_y = action.get("from_y") or action.get("y")
                to_x = action.get("to_x")
                to_y = action.get("to_y")
                button = action.get("button", "left")
                duration = action.get("duration", 0.5)

                if from_x is not None and from_y is not None and to_x is not None and to_y is not None:
                    if (0 <= from_x <= self.display_width and 0 <= from_y <= self.display_height and
                        0 <= to_x <= self.display_width and 0 <= to_y <= self.display_height):
                        pyautogui.moveTo(from_x, from_y)
                        pyautogui.drag(to_x - from_x, to_y - from_y, duration=duration, button=button)
                        print(f"✓ Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y})")
                        return "success"
                    else:
                        print(f"✗ Drag coordinates out of bounds")
                        return "error: coordinates out of bounds"
                else:
                    print(f"✗ Drag requires from_x, from_y, to_x, to_y")
                    return "error: missing drag coordinates"

            elif action_type == "type":
                text = action.get("text", "")
                pyautogui.write(text, interval=0.05)
                print(f"✓ Typed text: {text[:50]}...")
                # Small delay after typing to let the app process input
                time.sleep(0.2)
                return "success"

            elif action_type == "keypress":
                keys = action.get("keys", [])
                if not keys:
                    return "error: no keys specified"

                # Map key names to pyautogui format
                def map_key(key_name):
                    key_lower = key_name.lower()
                    key_map = {
                        "strg": "ctrl",
                        "ctrl": "ctrl",
                        "control": "ctrl",
                        "alt": "alt",
                        "option": "option",
                        "shift": "shift",
                        "enter": "enter",
                        "return": "enter",
                        "space": "space",
                        "tab": "tab",
                        "backspace": "backspace",
                        "delete": "delete",
                        "esc": "esc",
                        "escape": "esc",
                        "arrowup": "up",
                        "arrowdown": "down",
                        "arrowleft": "left",
                        "arrowright": "right",
                        "up": "up",
                        "down": "down",
                        "left": "left",
                        "right": "right"
                    }
                    return key_map.get(key_lower, key_name)

                # Map all keys
                mapped_keys = [map_key(k) for k in keys]

                # If multiple keys, use hotkey (for combinations like Cmd+A)
                if len(mapped_keys) > 1:
                    # CRITICAL: Add delay before keyboard shortcuts for Electron apps
                    # Electron/desktop apps need more time to be ready for shortcuts
                    time.sleep(0.5)

                    # Special handling for CTRL+A (select all)
                    if len(mapped_keys) == 2 and 'ctrl' in mapped_keys and 'a' in mapped_keys:
                        print(f"⚠️  Attempting CTRL+A with extra verification...")
                        # Try multiple times as fallback
                        for attempt in range(2):
                            pyautogui.hotkey(*mapped_keys)
                            time.sleep(0.3)
                            if attempt == 0:
                                print(f"   First attempt completed, trying again for reliability...")
                    else:
                        pyautogui.hotkey(*mapped_keys)

                    print(f"✓ Pressed key combination: {' + '.join(keys)}")
                    # Longer delay after keyboard shortcuts for Electron apps
                    time.sleep(0.5)
                else:
                    # Single key press
                    pyautogui.press(mapped_keys[0])
                    print(f"✓ Pressed key: {keys[0]}")

                return "success"

            elif action_type == "scroll":
                scroll_x = action.get("scroll_x", 0)
                scroll_y = action.get("scroll_y", 0)
                x = action.get("x", self.display_width // 2)
                y = action.get("y", self.display_height // 2)
                
                pyautogui.moveTo(x, y)
                # PyAutoGUI scroll: positive = up, negative = down
                # Computer use scroll_y: positive = down, negative = up
                pyautogui.scroll(-scroll_y // 1)  # Invert and scale
                print(f"✓ Scrolled at ({x}, {y}) with offset ({scroll_x}, {scroll_y})")
                return "success"

            elif action_type == "wait":
                print(f"✓ Waiting...")
                time.sleep(2)
                return "success"

            elif action_type == "screenshot":
                print(f"✓ Taking screenshot (automatic)")
                return "success"

            else:
                print(f"✗ Unknown action type: {action_type}")
                return f"error: unknown action type {action_type}"

        except Exception as e:
            print(f"✗ Error executing action: {e}")
            return f"error: {str(e)}"


class HospitalRunAgent:
    """Agent for navigating HospitalRun using OpenAI Computer Use"""

    def __init__(self):
        # Initialize OpenAI client with custom endpoint (truhn.ai)
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        
        if not api_key:
            raise ValueError("AZURE_OPENAI_API_KEY environment variable is required")
        
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.truhn.ai/openai/v1"
        )
        self.model = "computer-use-preview"
        
        print(f"Using endpoint: https://api.truhn.ai/openai/v1")
        print(f"Model: {self.model}\n")
        self.computer_tools = ComputerUseTools()
        self.previous_response_id: Optional[str] = None

    def _format_ocr_data(self, ocr_words: List[Dict[str, Any]]) -> str:
        """Format OCR data for inclusion in AI prompt"""
        if not ocr_words:
            return "OCR DATA: No text detected"

        # Group words by proximity (simple grouping by Y coordinate)
        # This helps identify multi-word buttons/labels
        grouped_text = []
        current_line = []
        last_y = None

        for word in ocr_words:
            # If Y coordinate changes significantly (new line), start new group
            if last_y is not None and abs(word['y'] - last_y) > 20:
                if current_line:
                    grouped_text.append(current_line)
                current_line = []

            current_line.append(word)
            last_y = word['y']

        if current_line:
            grouped_text.append(current_line)

        # Format for AI consumption
        ocr_output = "OCR DETECTED TEXT (with precise coordinates):\n"
        ocr_output += "Format: 'text' at (center_x, center_y) [confidence%]\n\n"

        for line in grouped_text:
            line_text = ' '.join([w['text'] for w in line])
            # Use the center of the entire line
            avg_x = sum(w['center_x'] for w in line) // len(line)
            avg_y = sum(w['center_y'] for w in line) // len(line)
            avg_conf = sum(w['confidence'] for w in line) // len(line)

            ocr_output += f"'{line_text}' at ({avg_x}, {avg_y}) [{avg_conf}%]\n"

        ocr_output += f"\nTotal: {len(ocr_words)} words detected\n"
        ocr_output += "TIP: Use OCR coordinates for clicking text/buttons for maximum accuracy!"

        return ocr_output

    def create_initial_instruction(self, user_task: str) -> str:
        """Create comprehensive initial instruction for computer control"""
        display_w = self.computer_tools.display_width
        display_h = self.computer_tools.display_height



        return f"""You are an expert AI agent controlling a computer to complete tasks through visual observation and automated actions.

=== YOUR TASK ===
{user_task}

=== DISPLAY & COORDINATE SYSTEM ===
- Screen resolution: {display_w}x{display_h} pixels
- Coordinate system: (0, 0) is top-left corner, ({display_w}, {display_h}) is bottom-right
- ALL coordinates you provide MUST be within these bounds
- Always aim for the CENTER of UI elements (buttons, links, input fields)

=== UI PATTERNS & BEST PRACTICES ===

**Form Field Location (CRITICAL):**
- Text input fields are MOSTLY positioned BELOW their field labels (typically 20-50 pixels down)
- Example: "First Name" label at Y=100 → input field at Y=130-150
- Multi-word labels: Use the horizontal center (avg X) of the entire label text
- Side-by-side fields: Same Y coordinate for labels, different X coordinates
- NEVER click directly on label text - always click BELOW where the input box is visible
- If some kind of date is beeing expected, do not use the date picker, just type the date directly into the field in the specified format

**OCR Data Usage:**
- You will receive OCR text detection with precise coordinates for all visible text
- Format: 'text' at (center_x, center_y) [confidence%]
- ALWAYS use OCR coordinates when clicking on buttons, links, or labels
- OCR is more accurate than visual estimation - PREFER OCR COORDINATES

**Clicking Strategy:**
1. Identify target element in OCR data
2. For buttons/links: Use exact OCR center coordinates
3. For input fields: Find label in OCR, then click 30-50px below at same X
4. Verify element is clickable and visible in screenshot before clicking
5. If click fails, adjust coordinates slightly and retry

**Text Input Verification (MANDATORY):**
After typing ANY text:
1. Wait 1-2 seconds for UI to update
2. Take a new screenshot
3. Use OCR to verify the EXACT text appears in the field
4. Check for common issues:
   - Text in wrong field
   - Text truncated or cut off
   - Special characters not entered
   - Auto-complete interference
5. If verification fails:
   - Click field again to ensure focus
   - Clear field (Cmd+A, then Delete/Backspace)
   - Re-type with slower interval
   - Verify again
6. NEVER proceed to next step without successful verification

**Navigation & Page Loading:**
- After clicking navigation items, WAIT 2-3 seconds for page load
- Verify page loaded by checking for expected elements in OCR/screenshot
- Breadcrumbs often appear at top showing current location

**Form Submission:**
- Before submitting ANY form, verify ALL required fields are filled correctly
- Check each field value appears in OCR data
- Look for "Save", "Submit", "Create", "Add" buttons at bottom of forms
- After submission, verify success message or navigation to result page

=== 🚨 CRITICAL: TASK COMPLETION REQUIREMENTS 🚨 ===

**YOU MUST NOT STOP OR DECLARE TASK COMPLETE UNTIL ALL OF THE FOLLOWING ARE VERIFIED:**

1. **✓ Action Fully Executed**: The complete requested action has been performed
   - Not just one step - the ENTIRE task must be completed
   - All required data entered, all required actions taken

2. **✓ Success Confirmation Visible**: Clear evidence the action succeeded
   - Look for: "Successfully", "Saved", "Complete", green checkmarks, confirmation messages
   - New record/data appears in the application
   - Page navigates to success state

3. **✓ All Data Validated**: Every piece of entered data is correct
   - Use OCR to verify each field shows the right value
   - Check names, dates, numbers, dropdowns all match what was requested
   - No truncated text, no wrong fields, no missing information

4. **✓ No Errors Present**: No error messages, warnings, or incomplete states
   - No red error text visible
   - No "Required field" messages
   - No validation errors

5. **✓ Final State Reached**: You're on the expected ending page/view
   - Not stuck mid-form
   - Not on a loading screen
   - Not on an intermediate step

**STOPPING TOO EARLY IS A CRITICAL FAILURE. When in doubt, continue and verify!**

If you cannot verify ALL 5 points above, DO NOT STOP - continue with corrective actions.

=== ACTION EXECUTION ===
You control the computer through actions. Available actions are handled by the computer_use_preview tool.

Be methodical and thorough. Double-check your work. Verify every input. Only declare completion when task is fully verified.

OCR data and screenshots will be provided with each step to help you understand current state."""


    def process_user_instruction(self, instruction: str, max_iterations: int = 50) -> List[Dict[str, Any]]:
        """
        Process a user instruction using the Responses API with computer_use_preview tool

        Args:
            instruction: The task to accomplish
            max_iterations: Maximum number of action cycles to prevent infinite loops

        Returns:
            List of actions taken with their results
        """
        # Start time tracking
        import datetime
        start_time = time.time()
        start_datetime = datetime.datetime.now()

        print(f"\n{'='*80}")
        print(f"TASK: {instruction}")
        print(f"Started at: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}\n")

        actions_log = []
        self.previous_response_id = None

        # Token usage tracking
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        api_calls = 0

        # Take initial screenshot
        print("Taking initial screenshot...")
        screenshot_b64, ocr_words = self.computer_tools.take_screenshot()
        ocr_text = self._format_ocr_data(ocr_words)

        # Create initial request
        print("Sending initial request to computer-use-preview model...")
        try:
            response = self.client.responses.create(
                model=self.model,
                tools=[{
                    "type": "computer_use_preview",
                    "display_width": self.computer_tools.display_width,
                    "display_height": self.computer_tools.display_height,
                    "environment": "mac"
                }],
                input=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self.create_initial_instruction(instruction) + f"\n\n{ocr_text}"
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot_b64}"
                        }
                    ]
                }],
                reasoning={"summary": "concise"},
                truncation="auto"
            )

            self.previous_response_id = response.id

            # Track token usage from initial call
            api_calls += 1
            if hasattr(response, 'usage') and response.usage:
                total_input_tokens += getattr(response.usage, 'input_tokens', 0)
                total_output_tokens += getattr(response.usage, 'output_tokens', 0)
                total_tokens += getattr(response.usage, 'total_tokens', 0)

        except Exception as e:
            print(f"Error during initial API call: {e}")
            return [{"error": str(e)}]

        # Main loop
        for iteration in range(max_iterations):
            print(f"\n--- Iteration {iteration + 1} ---")

            # Check for computer_call in output
            computer_calls = [item for item in response.output if hasattr(item, 'type') and item.type == "computer_call"]

            if not computer_calls:
                # No computer call - check for text output
                text_outputs = [item for item in response.output if hasattr(item, 'type') and item.type == "text"]

                if text_outputs:
                    model_text = text_outputs[0].text
                    print(f"\nModel response: {model_text}")

                    # Check if the model explicitly says it's done
                    done_phrases = [
                        "task is complete",
                        "task has been completed",
                        "successfully completed",
                        "task completed",
                        "finished the task",
                        "all steps have been completed",
                        "verification complete and successful"
                    ]

                    if any(phrase in model_text.lower() for phrase in done_phrases):
                        print("\n✓ Model confirms task completion.")
                        break
                    else:
                        # Model provided text but no action - prompt it to continue
                        print("\n⚠️  Model provided text but no action. Re-prompting...")
                        try:
                            response = self.client.responses.create(
                                model=self.model,
                                previous_response_id=self.previous_response_id,
                                tools=[{
                                    "type": "computer_use_preview",
                                    "display_width": self.computer_tools.display_width,
                                    "display_height": self.computer_tools.display_height,
                                    "environment": "mac"
                                }],
                                input=[{
                                    "role": "user",
                                    "content": [{
                                        "type": "input_text",
                                        "text": "Please provide your next ACTION as a computer_call. What specific action should be taken right now?"
                                    }]
                                }],
                                reasoning={"summary": "concise"},
                                truncation="auto"
                            )
                            self.previous_response_id = response.id

                            # Track token usage
                            api_calls += 1
                            if hasattr(response, 'usage') and response.usage:
                                total_input_tokens += getattr(response.usage, 'input_tokens', 0)
                                total_output_tokens += getattr(response.usage, 'output_tokens', 0)
                                total_tokens += getattr(response.usage, 'total_tokens', 0)

                            continue  # Go back to top of loop with new response
                        except Exception as e:
                            print(f"Error re-prompting model: {e}")
                            break
                else:
                    print("\nNo computer calls or text output. Task appears complete.")
                    break

            # Process the computer call
            computer_call = computer_calls[0]
            
            # Show reasoning if present
            reasoning_items = [item for item in response.output if hasattr(item, 'type') and item.type == "reasoning"]
            if reasoning_items and hasattr(reasoning_items[0], 'summary'):
                for summary_item in reasoning_items[0].summary:
                    if hasattr(summary_item, 'text'):
                        print(f"Reasoning: {summary_item.text}")

            # Check for pending safety checks
            pending_checks = getattr(computer_call, 'pending_safety_checks', [])
            acknowledged_checks = []
            
            if pending_checks:
                print(f"\n⚠️  Safety checks detected:")
                for check in pending_checks:
                    print(f"  - {check.code}: {check.message}")
                    # Auto-acknowledge for demo purposes (in production, require user confirmation)
                    acknowledged_checks.append({
                        "id": check.id,
                        "code": check.code,
                        "message": check.message
                    })
                print("  Acknowledging safety checks...\n")

            # Execute the action
            action = computer_call.action
            call_id = computer_call.call_id
            
            print(f"Action: {action.type}")
            if hasattr(action, 'x') and hasattr(action, 'y'):
                print(f"  Position: ({action.x}, {action.y})")
            if hasattr(action, 'text'):
                print(f"  Text: {action.text}")
            if hasattr(action, 'keys'):
                print(f"  Keys: {action.keys}")

            # Convert action object to dict for execute_action
            action_dict = {"type": action.type}
            for attr in ['x', 'y', 'button', 'text', 'keys', 'scroll_x', 'scroll_y', 'from_x', 'from_y', 'to_x', 'to_y', 'duration']:
                if hasattr(action, attr):
                    action_dict[attr] = getattr(action, attr)

            execution_result = self.computer_tools.execute_action(action_dict)
            
            actions_log.append({
                "iteration": iteration + 1,
                "action": action_dict,
                "result": execution_result
            })

            # Wait for action to complete
            time.sleep(1)

            # Take new screenshot
            print("Taking screenshot after action...")
            screenshot_b64, ocr_words = self.computer_tools.take_screenshot()
            ocr_text = self._format_ocr_data(ocr_words)

            # Send computer_call_output back to model with OCR data and continuation prompt
            try:
                # Build input with both computer_call_output and follow-up text prompt
                input_items = [
                    {
                        "call_id": call_id,
                        "type": "computer_call_output",
                        "acknowledged_safety_checks": acknowledged_checks,
                        "output": {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot_b64}"
                        }
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"""Action completed. Here's the updated screen state:

{ocr_text}

🚨 CRITICAL REMINDER - DO NOT STOP UNLESS TASK IS FULLY COMPLETE:

Before returning NO action (which ends the task), you MUST verify:
✓ The COMPLETE requested task has been executed (not just one step)
✓ Success confirmation is visible (save message, new record shown, etc.)
✓ ALL data fields are correctly filled and validated
✓ NO error messages or warnings are present
✓ You are on the final expected page/view (not mid-process)

If ANY of the above is not true, you MUST continue with the next action.

REMEMBER: Returning no computer_call = declaring task complete = ONLY do this when EVERYTHING above is verified.

Current state analysis:
- What was the original task?
- What have we done so far?
- What still needs to be done?
- Is there a success message visible?

Based on this analysis, what is your NEXT ACTION? (Provide a computer_call)"""
                            }
                        ]
                    }
                ]

                response = self.client.responses.create(
                    model=self.model,
                    previous_response_id=self.previous_response_id,
                    tools=[{
                        "type": "computer_use_preview",
                        "display_width": self.computer_tools.display_width,
                        "display_height": self.computer_tools.display_height,
                        "environment": "mac"
                    }],
                    input=input_items,
                    reasoning={"summary": "concise"},
                    truncation="auto"
                )

                self.previous_response_id = response.id

                # Track token usage
                api_calls += 1
                if hasattr(response, 'usage') and response.usage:
                    total_input_tokens += getattr(response.usage, 'input_tokens', 0)
                    total_output_tokens += getattr(response.usage, 'output_tokens', 0)
                    total_tokens += getattr(response.usage, 'total_tokens', 0)

            except Exception as e:
                print(f"Error during API call: {e}")
                actions_log.append({
                    "iteration": iteration + 1,
                    "error": str(e)
                })
                break

        # Calculate and print task duration
        end_time = time.time()
        duration_seconds = end_time - start_time
        end_datetime = datetime.datetime.now()

        # Format duration in human-readable format
        minutes = int(duration_seconds // 60)
        seconds = duration_seconds % 60

        print(f"\n{'='*80}")
        print(f"TASK COMPLETION SUMMARY")
        print(f"{'='*80}")
        print(f"Task:     {instruction}")
        print(f"Started:  {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Finished: {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Duration: {minutes}m {seconds:.2f}s ({duration_seconds:.2f} seconds)")
        print(f"Actions:  {len(actions_log)} actions completed")
        print(f"\nTOKEN USAGE:")
        print(f"  API Calls:      {api_calls}")
        print(f"  Input Tokens:   {total_input_tokens:,}")
        print(f"  Output Tokens:  {total_output_tokens:,}")
        print(f"  Total Tokens:   {total_tokens:,}")
        print(f"{'='*80}\n")

        return actions_log

    def interactive_mode(self):
        """Run the agent in interactive mode where user can give multiple instructions"""
        print("\n" + "="*80)
        print("HOSPITALRUN NAVIGATION AGENT (Computer Use Preview)")
        print("="*80)
        print("\nThis agent uses OpenAI's computer-use-preview model to navigate HospitalRun.")
        print("Give it instructions and it will use computer vision and automation to complete them.")
        print("\nCommands:")
        print("  - Type your instruction to execute a task")
        print("  - Type 'quit' or 'exit' to stop")
        print("="*80 + "\n")

        while True:
            try:
                instruction = input("\nWhat would you like the agent to do? > ").strip()

                if instruction.lower() in ['quit', 'exit', 'q']:
                    print("Exiting agent...")
                    break

                if not instruction:
                    continue

                # Process the instruction (summary is now printed within this method)
                actions_log = self.process_user_instruction(instruction)

            except KeyboardInterrupt:
                print("\n\nInterrupted by user. Exiting...")
                break
            except Exception as e:
                print(f"\nError: {e}")


def main():
    """Main entry point"""
    agent = HospitalRunAgent()
    agent.interactive_mode()


if __name__ == "__main__":
    main()
