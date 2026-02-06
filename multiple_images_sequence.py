import asyncio
import glob
import os
import signal
import sys
import threading
import time
from io import BytesIO

from flask import Flask, Response, render_template_string, jsonify
from PIL import Image
from odyssey import Odyssey, OdysseyAuthError, OdysseyConnectionError

app = Flask(__name__)

# Global variables for frame sharing
current_frame = None
frame_lock = threading.Lock()
stream_active = False
should_shutdown = False
current_prompt = ""

# Configuration
API_KEY = os.environ["API_KEY"]
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "on_the_phone")
PROMPTS = [
    "a person on the left is talking on the phone, while the person on the right is listening attentively",
]
SECONDS_PER_IMAGE = 10
TOTAL_DURATION = 60


def load_images():
    """Load all PNG images from under_the_bridge folder."""
    paths = sorted(glob.glob(os.path.join(IMAGE_DIR, "*.png")))
    if not paths:
        print(f"No PNG images found in {IMAGE_DIR}")
        sys.exit(1)
    print(f"Found {len(paths)} images:")
    for p in paths:
        print(f"  {os.path.basename(p)}")
    return paths


def save_frame(frame):
    """Callback to save the latest frame."""
    global current_frame
    with frame_lock:
        current_frame = frame


def generate_frames():
    """Generator function to yield frames as MJPEG."""
    while True:
        with frame_lock:
            if current_frame is not None:
                img = Image.fromarray(current_frame.data)
                buffer = BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                frame_bytes = buffer.getvalue()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                )
        time.sleep(0.033)  # ~30 fps


async def run_odyssey():
    """Run the Odyssey client, rotating through images."""
    global stream_active, should_shutdown, current_prompt

    image_paths = load_images()
    client = Odyssey(api_key=API_KEY)

    try:
        await client.connect(
            on_video_frame=save_frame,
            on_stream_started=lambda sid: print(f"Stream ready: {sid}"),
        )

        stream_active = True
        elapsed = 0
        image_index = 0

        while elapsed < TOTAL_DURATION and not should_shutdown:
            path = image_paths[image_index % len(image_paths)]
            prompt = PROMPTS[image_index % len(PROMPTS)]
            current_prompt = prompt
            print(f"[{elapsed}s] {os.path.basename(path)} | {prompt[:60]}...")

            await client.start_stream(prompt, portrait=False, image=path)
            await asyncio.sleep(SECONDS_PER_IMAGE)
            await client.end_stream()

            elapsed += SECONDS_PER_IMAGE
            image_index += 1

        print(f"Completed {elapsed}s of streaming across {image_index} segments")

    except OdysseyAuthError:
        print("Invalid API key")
    except OdysseyConnectionError as e:
        print(f"Connection failed: {e}")
    except asyncio.CancelledError:
        print("Stream cancelled")
    finally:
        await client.disconnect()
        stream_active = False
        print("Done")


def start_odyssey_thread():
    """Start Odyssey in a separate thread."""
    try:
        asyncio.run(run_odyssey())
    except Exception as e:
        print(f"Odyssey error: {e}")
        import traceback
        traceback.print_exc()


@app.route("/")
def index():
    """Main page with video player."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Under the Bridge</title>
        <style>
            body {
                margin: 0;
                padding: 20px;
                background: #1a1a1a;
                color: white;
                font-family: Arial, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }
            h1 { margin-bottom: 20px; }
            img {
                max-width: 90vw;
                max-height: 80vh;
                border: 2px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
            }
            .status {
                margin-top: 20px;
                padding: 10px 20px;
                background: #333;
                border-radius: 4px;
            }
            .status.active { background: #4CAF50; }
            .prompt {
                margin-top: 12px;
                padding: 10px 20px;
                background: #2a2a2a;
                border-left: 3px solid #4CAF50;
                border-radius: 4px;
                font-size: 14px;
                max-width: 600px;
                text-align: left;
                color: #ccc;
                min-height: 1.2em;
            }
        </style>
    </head>
    <body>
        <h1>Under the Bridge</h1>
        <img src="/video_feed" alt="Video Stream">
        <div class="status" id="status">Waiting for stream...</div>
        <div class="prompt" id="prompt"></div>
        <script>
            setInterval(async () => {
                try {
                    const res = await fetch('/stream_status');
                    const data = await res.json();
                    const el = document.getElementById('status');
                    const promptEl = document.getElementById('prompt');
                    if (data.active) {
                        el.textContent = 'Stream Active';
                        el.className = 'status active';
                        promptEl.textContent = data.prompt || '';
                    } else {
                        el.textContent = 'Stream Ended';
                        el.className = 'status';
                        promptEl.textContent = '';
                    }
                } catch (e) {}
            }, 1000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@app.route("/stream_status")
def stream_status():
    return jsonify({"active": stream_active, "prompt": current_prompt})


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def signal_handler(sig, frame):
    global should_shutdown, stream_active
    print("\nShutting down...")
    should_shutdown = True
    stream_active = False
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    odyssey_thread = threading.Thread(target=start_odyssey_thread, daemon=True)
    odyssey_thread.start()

    print("Starting web server at http://127.0.0.1:5001")
    print(f"Rotating {SECONDS_PER_IMAGE}s per image, {TOTAL_DURATION}s total")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
    except KeyboardInterrupt:
        should_shutdown = True
        stream_active = False
