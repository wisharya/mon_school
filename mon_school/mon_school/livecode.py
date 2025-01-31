"""Interaction with livecode.
"""
from __future__ import annotations
import frappe
import json
import html
from urllib.parse import urlparse
import websocket
from ..joy.build import get_livecode_files

@frappe.whitelist(allow_guest=True)
def execute(code: str, is_sketch=False, context=None) -> LiveCodeResult:
    """Executes the code and returns the output.

    The return value will be of the following format:

    {
        "status": "success",
        "output": [
            "1\n",
            "2\n"
        ],
        "shapes": [
            {"tag": "circle", "cx": 0, "cy": 0, "r": 50, "fill": "red"},
            {"tag": "rect", "x": -50, "y": 50, "width": 50, "height": 50, "fill": "red"}
        ]
    }
    """
    livecode_url = frappe.get_cached_doc("LMS Settings").livecode_url
    livecode = LiveCode(livecode_url)
    result = livecode.execute(code, is_sketch=is_sketch)
    record_code_run(code, result, context)
    return result.as_dict()

def record_code_run(code, result, context=None):
    """Records the code execution.
    """
    context = context or {}

    course = context.get("course")
    lesson = context.get("lesson")
    batch = context.get("batch")
    sketch = context.get("sketch")
    exercise = context.get("exercise")
    example = context.get("example")

    if sketch is not None:
        source_type = "Sketch"
    elif exercise is not None:
        source_type = "Exercise"
    else:
        source_type = "Example"

    if result.status == "failed":
        failure_type, error = result.find_exception_details()
    else:
        failure_type, error = None, None

    try:
        doc = frappe.get_doc({
            "doctype": "Code Run",
            "code": code,
            "result": json.dumps(result.as_dict(), indent="  "),
            "status": result.status.title(), # status is Success|Failed in the db
            "error": result.error_code,
            "course": course,
            "batch": batch,
            "lesson": lesson,
            "source_type": source_type,
            "sketch": sketch,
            "exercise": exercise,
            "example": example,
            "failure_type": failure_type,
            "error": error
        })
        doc.save(ignore_permissions=True)
        print(f"recorded code run {doc.name}")
    except Exception:
        print("Failed to save code run")
        import traceback
        traceback.print_exc()

def livecode_to_svg(code, is_sketch=False):
    """Renders the code as svg.
    """
    result = execute(code, is_sketch=is_sketch)
    if result.get('status') != 'success':
        return None

    return _render_svg(result['shapes'])

def _render_svg(shapes):
    return (
        '<svg width="300" height="300" viewBox="-150 -150 300 300" fill="none" stroke="black" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">\n'
        + "\n".join(_render_shape(s) for s in shapes)
        + '\n'
        + '</svg>\n')

def _render_shape(node):
    tag = node.pop("tag")
    children = node.pop("children", None)

    items = [(k.replace("_", "-"), html.escape(str(v))) for k, v in node.items() if v is not None]
    attrs = " ".join(f'{name}="{value}"' for name, value in items)

    if children:
        children_svg = "\n".join(_render_shape(c) for c in children)
        return f"<{tag} {attrs}>{children_svg}</{tag}>"
    else:
        return f"<{tag} {attrs} />"

class LiveCodeResult:
    def __init__(self):
        self.status = "success"
        self.error_code = None
        self.output = []
        self.shapes = []

    def mark_failed(self, error_code):
        self.status = "failed"
        self.error_code = error_code

    def find_exception_details(self):
        """Returns the exception type and the exception message.
        """
        if self.status != "failed" or not self.output:
            return None, None

        output = "".join(self.output)

        # not an exception
        if "Traceback (most recent call last):" not in output:
            return None, None


        if ":" in self.output[-1]:
            exctype, message = self.output[-1].split(":", 1)

        return exctype.strip(), message.strip()

    def add_output(self, output):
        self.output.append(output)

    def add_shape(self, shape):
        self.shapes.append(shape)

    def as_dict(self):
        return dict(self.__dict__)

    def _render_shape(self, shape):
        tag = shape

    def as_svg(self):
        return (
            '<svg width="300" height="300" viewBox="-150 -150 300 300" fill="none" stroke="black" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">\n'
            + "\n".join(self._render_shape(s) for s in self.shapes)
            + '\n'
            + '</svg>\n')

class LiveCode:
    def __init__(self, livecode_url, timeout=3):
        self.livecode_url = livecode_url
        self.timeout = timeout

    def get_livecode_ws_url(self):
        url = urlparse(self.livecode_url)
        protocol = "wss" if url.scheme == "https" else "ws"
        return protocol + "://" + url.netloc + "/livecode"

    def execute(self, code, is_sketch=False):
        result = LiveCodeResult()
        try:
            ws = self.get_websocket()
        except (IOError, websocket.WebSocketException):
            result.mark_failed("connection-failed")
            return result

        env = {}
        if is_sketch:
            env['SKETCH'] = "yes"

        msg = {
            "msgtype": "exec",
            "runtime": "python",
            "code": code,
            "env": env,
            "files": get_livecode_files(),
            "command": ["python", "start.py"]
        }
        exit_status = -1
        try:
            ws.send(json.dumps(msg))
            messages = self._read_messages(ws)

            for m in messages:
                if m['msgtype'] == 'write':
                    result.add_output(m['data'])
                elif m['msgtype'] == 'shape':
                    result.add_shape(m['shape'])
                elif m['msgtype'] == 'exitstatus':
                    exit_status = m['exitstatus']
        except (IOError, websocket.WebSocketException):
            result.mark_failed('connection-reset')

        if exit_status != 0:
            result.status = "failed"
        return result

    def get_websocket(self):
        ws = websocket.WebSocket()
        ws.settimeout(self.timeout)
        livecode_ws_url = self.get_livecode_ws_url()
        ws.connect(livecode_ws_url)
        return ws

    def _read_messages(self, ws):
        messages = []
        try:
            while True:
                msg = ws.recv()
                if not msg:
                    break
                messages.append(json.loads(msg))
        except websocket.WebSocketTimeoutException as e:
            print("Error:", e)
            pass
        return messages