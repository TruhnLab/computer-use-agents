#coding: utf-8

import os
import sys
import threading
import io
import asyncio
import tempfile
from contextlib import redirect_stdout
import base64
import json
import pyautogui
import pyperclip
from mcp.server.fastmcp import Image
import PIL
import pygetwindow as gw
import requests
from typing import Any

omniparser_path = os.path.join(os.path.dirname(__file__), '..', '..', 'OmniParser')
sys.path = [omniparser_path, ] + sys.path
from util.omniparser import Omniparser
sys.path = sys.path[1:]

INPUT_IMAGE_SIZE = 960
TARGET_WINDOW_NAME = "HospitalRun"
os.environ['TARGET_WINDOW_NAME'] = TARGET_WINDOW_NAME

def mcp_autogui_main(mcp):
    global omniparser
    omniparser = None
    input_image_path = ''
    output_dir_path = ''
    omniparser_thread = None
    result_image = None
    input_image_resized_path = None
    detail = None
    is_finished = False

    current_mouse_x, current_mouse_y = pyautogui.position()
    is_set_target_window = False
    match_windows = None
    if 'TARGET_WINDOW_NAME' in os.environ:
        match_windows = gw.getWindowsWithTitle(os.environ['TARGET_WINDOW_NAME'])
    if match_windows:
        current_window = match_windows[0]
        is_set_target_window = True
        print(f'Target window is set to "{current_window.title}".', file=sys.stderr)
    else:
        current_window = gw.getActiveWindow()
    with redirect_stdout(sys.stderr):
        config = {
            'som_model_path': os.environ['SOM_MODEL_PATH'] if 'SOM_MODEL_PATH' in os.environ else os.path.join(omniparser_path, 'weights/icon_detect/model.pt'),
            'caption_model_name': os.environ['CAPTION_MODEL_NAME'] if 'CAPTION_MODEL_NAME' in os.environ else 'florence2',
            'caption_model_path': os.environ['CAPTION_MODEL_PATH'] if 'CAPTION_MODEL_PATH' in os.environ else os.path.join(omniparser_path, 'weights/icon_caption_florence'),
            'device': os.environ['OMNI_PARSER_DEVICE'] if 'OMNI_PARSER_DEVICE' in os.environ else 'cuda',
            'BOX_TRESHOLD': float(os.environ['BOX_TRESHOLD']) if 'BOX_TRESHOLD' in os.environ else 0.05,
        }

        if not 'OMNI_PARSER_SERVER' in os.environ:
            def omniparser_start_thread_func():
                global omniparser

                sys.path = [os.path.join(os.path.dirname(__file__), '..', '..'), ] + sys.path
                from download_models import download_omniparser_models
                download_omniparser_models()
                sys.path = sys.path[1:]

                omniparser = Omniparser(config)
                #print('Loading Omniparser is finished.', file=sys.stderr)
            if 'OMNI_PARSER_BACKEND_LOAD' in os.environ and os.environ['OMNI_PARSER_BACKEND_LOAD']:
                omniparser_start_thread = threading.Thread(target=omniparser_start_thread_func)
                omniparser_start_thread.start()
            else:
                omniparser_start_thread_func()
        
    temp_dir = tempfile.TemporaryDirectory()
    dname = temp_dir.name

    @mcp.tool()
    async def omniparser_details_on_screen() -> list:
        """Get the screen and analyze its details.
If a timeout occurs, you can continue by running it again.

Return value:
    - Details such as the content of text.
    - Screen capture with ID number added.
"""
        nonlocal omniparser_thread, result_image, detail, is_finished

        if not 'OMNI_PARSER_SERVER' in os.environ:
            while omniparser is None:
                await asyncio.sleep(0.1)

        detail_text = ''
        with redirect_stdout(sys.stderr):
            def omniparser_thread_func():
                nonlocal result_image, detail, is_finished, detail_text
                with redirect_stdout(sys.stderr):

                    screenshot_image = pyautogui.screenshot()

                    if is_set_target_window:
                        screenshot_image = screenshot_image.crop((current_window.left, current_window.top, current_window.right, current_window.bottom))

                    if 'OMNI_PARSER_SERVER' in os.environ:
                        buffered = io.BytesIO()
                        screenshot_image.save(buffered, format='png')
                        send_img = base64.b64encode(buffered.getvalue()).decode('ascii')
                        json_data = json.dumps({'base64_image': send_img})
                        response = requests.post(
                            f"http://{os.environ['OMNI_PARSER_SERVER']}/parse/",
                            data=json_data,
                            headers={"Content-Type": "application/json"}
                        )
                        response_json = response.json()
                        dino_labled_img = response_json['som_image_base64']
                        detail = response_json['parsed_content_list']
                    else:
                        dino_labled_img, detail = omniparser.parse_raw(screenshot_image)

                    image_bytes = base64.b64decode(dino_labled_img)
                    result_image_local = PIL.Image.open(io.BytesIO(image_bytes))

                    # Save to a fixed directory
                    desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
                    fixed_save_path = os.path.join(desktop_path, "latest_omniparser_analysis.png")
                    result_image_local.save(fixed_save_path)

                    width, height = result_image_local.size
                    if width > height:
                        result_image_local = result_image_local.resize((INPUT_IMAGE_SIZE, INPUT_IMAGE_SIZE * height // width))
                    else:
                        result_image_local = result_image_local.resize((INPUT_IMAGE_SIZE * width // height, INPUT_IMAGE_SIZE))

                    result_image = io.BytesIO()
                    result_image_local.save(result_image, format='png')

                    detail_text = ''
                    for loop, content in enumerate(detail):
                        detail_text += f'ID: {loop}, {content["type"]}: {content["content"]}'

                        # Add positional information of each element
                        compos = content['bbox']
                        bbox_str = f"[x:({compos[0]:.3f}:{compos[2]:.3f}), y:({compos[1]:.3f}:{compos[3]:.3f})]"
                        detail_text += f", Relative Bounding Box Position: {bbox_str}\n"

                    is_finished = True
            if omniparser_thread is None:
                result_image = None
                detail = None
                is_finished = False
                omniparser_thread = threading.Thread(target=omniparser_thread_func)
                omniparser_thread.start()
            
            while not is_finished:
                await asyncio.sleep(0.1)

            omniparser_thread = None

            return [detail_text]#, Image(data=result_image.getvalue(), format="png")]

    @mcp.tool()
    async def omniparser_click(id: int, button: str = 'left', clicks: int = 1) -> bool:
        """Click on anything on the screen.

Args:
    id: The element on the screen that it click. You can check it with "omniparser_details_on_screen".
    button: Button to click. 'left', 'middle', or 'right'.
    clicks: Number of clicks. 2 for double click.
Return value:
    True is success. False is means "this is not found".
"""
        nonlocal current_mouse_x, current_mouse_y, current_window
        if len(detail) > id:
            if is_set_target_window:
                # current_window.activate()
                left = current_window.left
                top = current_window.top
                screen_width, screen_height = current_window.width, current_window.height
            else:
                left = 0
                top = 0
                screen_width, screen_height = pyautogui.size()
            compos = detail[id]['bbox']
            current_mouse_x = int((compos[0] + compos[2]) * screen_width) // 2 + left
            current_mouse_y = int((compos[1] + compos[3]) * screen_height) // 2 + top
            pyautogui.click(x=current_mouse_x, y=current_mouse_y, button=button, clicks=clicks)
            if not is_set_target_window:
                current_window = gw.getActiveWindow()
            return True
        return False

    @mcp.tool()
    async def omniparser_drags(from_id: int, to_id: int, button: str = 'left', key: str = '') -> bool:
        """Drag and drop on the screen.

Args:
    from_id: The element on the screen that it start to drag. You can check it with "omniparser_details_on_screen".
    to_id: The element on the screen that it end to drag. You can check it with "omniparser_details_on_screen".
    button: Button to click. 'left', 'middle', or 'right'.
    key: The name of the keyboard key if you hold down it while dragging. You can check key's name with "omniparser_get_keys_list".
Return value:
    True is success. False is means "this is not found".
"""
        nonlocal current_mouse_x, current_mouse_y, current_window

        if is_set_target_window:
            # current_window.activate()
            left = current_window.left
            top = current_window.top
            screen_width, screen_height = current_window.width, current_window.height
        else:
            left = 0
            top = 0
            screen_width, screen_height = pyautogui.size()

        from_x = -1
        to_x = -1
        if len(detail) <= from_id or len(detail) <= to_id:
            return False
        compos = detail[from_id]['bbox']
        from_x = int((compos[0] + compos[2]) * screen_width) // 2 + left
        from_y = int((compos[1] + compos[3]) * screen_height) // 2 + top
        compos = detail[to_id]['bbox']
        to_x = int((compos[0] + compos[2]) * screen_width) // 2 + left
        to_y = int((compos[1] + compos[3]) * screen_height) // 2 + top

        if key is not None and key != '':
            pyautogui.keyDown(key)
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, button=button)
        if key is not None and key != '':
            pyautogui.keyUp(key)
        current_mouse_x = to_x
        current_mouse_y = to_y
        if not is_set_target_window:
            current_window = gw.getActiveWindow()
        return True

    @mcp.tool()
    async def omniparser_mouse_move(id: int) -> bool:
        """Moves the mouse cursor over the specified element.

Args:
    id: The element on the screen that it move. You can check it with "omniparser_details_on_screen".
Return value:
    True is success. False is means "this is not found".
"""
        nonlocal current_mouse_x, current_mouse_y, current_window
        if len(detail) <= id:
            return False
        compos = detail[id]['bbox']
        if is_set_target_window:
            # current_window.activate()
            left = current_window.left
            top = current_window.top
            screen_width, screen_height = current_window.width, current_window.height
        else:
            left = 0
            top = 0
            screen_width, screen_height = pyautogui.size()
        current_mouse_x = int((compos[0] + compos[2]) * screen_width) // 2 + left
        current_mouse_y = int((compos[1] + compos[3]) * screen_height) // 2 + top
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        if not is_set_target_window:
            current_window = gw.getActiveWindow()
        return True

    @mcp.tool()
    async def omniparser_scroll(clicks: int) -> None:
        """The mouse scrolling wheel behavior.

Args:
    clicks: Amount of scrolling. 1000 is scroll up 1000 "clicks" and -1000 is scroll down 1000 "clicks".
"""
        # current_window.activate()
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        pyautogui.scroll(clicks)

    @mcp.tool()
    async def omniparser_write(content: Any, id: int = -1) -> None:
        """Type the characters in the string that is passed.

Args:
    content: What to enter.
    id: Click on the target before typing. You can check it with "omniparser_details_on_screen".
"""
        # Allow the caller to pass numbers or other non-string types
        # Pydantic validation (used by the MCP tooling layer) may pass non-strings
        # so coerce to a string here to avoid validation errors.
        if id >= 0:
            await omniparser_click(id)
        else:
            # current_window.activate()
            pyautogui.moveTo(current_mouse_x, current_mouse_y)

        # Normalize content to a string
        if content is None:
            content_str = ''
        else:
            content_str = str(content)

        if content_str.isascii():
            pyautogui.write(content_str)
        else:
            prev_clip = pyperclip.paste()
            pyperclip.copy(content_str)
            pyautogui.hotkey('ctrl', 'v')
            if prev_clip:
                pyperclip.copy(prev_clip)

    @mcp.tool()
    async def omniparser_get_keys_list() -> list[str]:
        """List of keyboard keys. Used in "omniparser_input_key" etc.

Return value:
    List of keyboard keys.
"""
        return pyautogui.KEYBOARD_KEYS

    @mcp.tool()
    async def omniparser_input_key(key1: str, key2: str = '', key3: str = '') -> None:
        """Press of keyboard keys. 

Args:
    key1-3: Press of keyboard keys. You can check key's name with "omniparser_get_keys_list". If you specify multiple, keys will be pressed down in order, and then released in reverse order.
"""
        # current_window.activate()
        pyautogui.moveTo(current_mouse_x, current_mouse_y)
        if key2 is not None and key2 != '' and key3 is not None and key3 != '':
            pyautogui.hotkey(key1, key2, key3)
        elif key2 is not None and key2 != '':
            pyautogui.hotkey(key1, key2)
        else:
            pyautogui.hotkey(key1)

    @mcp.tool()
    async def omniparser_wait(time: float = 1.0) -> None:
        """Waits for the specified number of seconds.

Args:
    time: Waiting time (seconds).
"""
        await asyncio.sleep(time)
