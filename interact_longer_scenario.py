import asyncio
import glob
import os
import random
import signal
import sys
import threading
import time
from io import BytesIO

import fal_client
import numpy as np
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
current_pair_label = ""
fal_status = ""

# Configuration
API_KEY = os.environ["API_KEY"]

IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "on_the_phone")
PEOPLE_DIR = os.path.join(IMAGE_DIR, "more_people")
INITIAL_IMAGE = os.path.join(IMAGE_DIR, "01_02.png")

TOTAL_DURATION = 200
SECONDS_PER_STEP = 4

INTERACTION_PROMPTS = [
    "Two people are talking on the phone, one is speaking animatedly while the other listens with interest",
    "One person surprises the other with a gift, both are laughing and reacting with excitement",
    "The person on the right leaves his frame and goes to the person on the left, they hug each other warmly, smiling and happy",
]

SECONDS_PER_SEGMENT = SECONDS_PER_STEP * len(INTERACTION_PROMPTS)

FAL_COMBINE_PROMPT = (
    "combine these two people calling on the phone into one image, "
    "there's a vertical line between them like a photo composition. "
    "imagine each of them in the room"
)



def load_people():
    """Load all individual person images from more_people/."""
    people = {}
    for path in sorted(glob.glob(os.path.join(PEOPLE_DIR, "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0]  # "01", "02", etc.
        people[name] = path
    print(f"Loaded {len(people)} people: {', '.join(people.keys())}")
    return people


async def generate_next_image(people, current_pair, pool):
    """Generate combined image for a new pair using async fal API."""
    global fal_status

    person_left, person_right = current_pair  # (left, right)
    keeper = random.choice([person_left, person_right])

    # Draw from pool without replacement, refill when exhausted
    candidates = [p for p in pool if p != person_left and p != person_right]
    if not candidates:
        refill = [p for p in people if p != person_left and p != person_right]
        random.shuffle(refill)
        pool.clear()
        pool.extend(refill)
        candidates = list(pool)

    newcomer = candidates[0]
    pool.remove(newcomer)

    # Keeper switches sides: left→right or right→left
    if keeper == person_left:
        new_pair = (newcomer, keeper)  # keeper was left, moves to right
    else:
        new_pair = (keeper, newcomer)  # keeper was right, moves to left

    print(f"[FAL] Keeper: {keeper}, Newcomer: {newcomer} → {new_pair[0]} (L) + {new_pair[1]} (R)")

    # Upload both images concurrently
    fal_status = f"Uploading {keeper} + {newcomer}..."
    print("[FAL] Uploading images...")
    url_keeper, url_newcomer = await asyncio.gather(
        fal_client.upload_file_async(people[keeper]),
        fal_client.upload_file_async(people[newcomer]),
    )
    print(f"[FAL] Uploaded: {url_keeper[:60]}... , {url_newcomer[:60]}...")

    # Arrange as [left, right] for fal — keeper switches sides
    if keeper == person_left:
        image_urls = [url_newcomer, url_keeper]
    else:
        image_urls = [url_keeper, url_newcomer]

    # Submit generation and stream events
    fal_status = f"Generating combined image ({new_pair[0]} (L) + {new_pair[1]} (R))..."
    print("[FAL] Generating combined image...")
    handler = await fal_client.submit_async(
        "fal-ai/flux-2/flash/edit",
        arguments={
            "prompt": FAL_COMBINE_PROMPT,
            "guidance_scale": 2.5,
            "image_size": "landscape_16_9",
            "num_images": 1,
            "enable_safety_checker": True,
            "output_format": "png",
            "image_urls": image_urls,
        },
    )

    async for event in handler.iter_events(with_logs=True):
        if isinstance(event, fal_client.InProgress):
            for log in event.logs or []:
                print(f"[FAL] {log['message']}")
        elif isinstance(event, fal_client.Queued):
            fal_status = f"Queued ({new_pair[0]} + {new_pair[1]})..."

    result = await handler.get()

    # Download the result image
    image_url = result["images"][0]["url"]
    print(f"[FAL] Got result: {image_url[:80]}...")

    fal_status = "Downloading result image..."
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            image_bytes = await resp.read()

    # Save to temp file for Odyssey
    temp_path = os.path.join(IMAGE_DIR, f"generated_{new_pair[0]}_{new_pair[1]}.png")
    with open(temp_path, "wb") as f:
        f.write(image_bytes)

    fal_status = "Image ready!"
    print(f"[FAL] Saved combined image: {temp_path}")
    # Direction: push away the departing person
    direction = "left" if keeper == person_right else "right"
    return temp_path, new_pair, direction


class SyntheticFrame:
    def __init__(self, data):
        self.data = data


def save_frame(frame):
    global current_frame
    with frame_lock:
        current_frame = frame


async def play_transition(last_frame_data, new_image_path, direction="left", duration=1.0, fps=30):
    """Play a push transition between the last stream frame and the new image."""
    global current_frame

    old_img = Image.fromarray(last_frame_data)
    new_img = Image.open(new_image_path).resize(old_img.size)

    width, height = old_img.size
    num_frames = int(duration * fps)
    frame_delay = duration / num_frames

    if direction == "left":
        canvas = Image.new("RGB", (width * 2, height))
        canvas.paste(old_img, (0, 0))
        canvas.paste(new_img, (width, 0))
    else:
        canvas = Image.new("RGB", (width * 2, height))
        canvas.paste(new_img, (0, 0))
        canvas.paste(old_img, (width, 0))

    for i in range(num_frames + 1):
        t = i / num_frames
        t = t * t * (3 - 2 * t)  # ease-in-out
        offset = int(t * width)

        if direction == "left":
            frame_img = canvas.crop((offset, 0, offset + width, height))
        else:
            frame_img = canvas.crop((width - offset, 0, width * 2 - offset, height))

        with frame_lock:
            current_frame = SyntheticFrame(np.array(frame_img))

        await asyncio.sleep(frame_delay)


def generate_frames():
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
        time.sleep(0.033)


async def run_odyssey():
    global stream_active, should_shutdown, current_prompt, current_pair_label, fal_status

    people = load_people()
    client = Odyssey(api_key=API_KEY)

    current_image = INITIAL_IMAGE
    current_pair = ("01", "02")
    elapsed = 0

    try:
        await client.connect(
            on_video_frame=save_frame,
            on_stream_started=lambda sid: print(f"Stream ready: {sid}"),
        )

        stream_active = True
        fal_task = None
        pool = list(people.keys())
        random.shuffle(pool)

        while elapsed < TOTAL_DURATION and not should_shutdown:
            current_pair_label = f"Person {current_pair[0]} + Person {current_pair[1]}"
            print(f"\n=== Segment at {elapsed}s | {current_pair_label} ===")

            # Only start a new fal task if there isn't one already running
            if fal_task is None:
                fal_task = asyncio.create_task(generate_next_image(people, current_pair, pool))
            else:
                fal_status = "Still running from previous segment..."
                print("[FAL] Previous generation still running, waiting...")

            # Run 30s segment: start_stream + 2 interactions at 10s intervals
            for i, prompt in enumerate(INTERACTION_PROMPTS):
                if should_shutdown:
                    break

                current_prompt = prompt
                step_time = elapsed + i * SECONDS_PER_STEP
                print(f"[{step_time}s] {'START' if i == 0 else 'INTERACT'}: {prompt[:70]}...")

                if i == 0:
                    await client.start_stream(prompt, portrait=False, image=current_image)
                else:
                    await client.interact(prompt)

                await asyncio.sleep(SECONDS_PER_STEP)

            await client.end_stream()
            elapsed += SECONDS_PER_SEGMENT

            # Check if fal image is ready
            if fal_task.done():
                try:
                    next_image, next_pair, direction = fal_task.result()

                    # Play push transition using last stream frame
                    with frame_lock:
                        last_data = current_frame.data if current_frame else None
                    if last_data is not None:
                        fal_status = f"Transitioning to {next_pair[0]} + {next_pair[1]}..."
                        await play_transition(last_data, next_image, direction)

                    current_image = next_image
                    current_pair = next_pair
                    fal_status = f"Switched to Person {next_pair[0]} + Person {next_pair[1]}"
                    print(f"[FAL] Switching to new pair: {next_pair}")
                except Exception as e:
                    fal_status = "Generation failed, reusing current pair"
                    print(f"[FAL] Generation failed, reusing current image: {e}")
                fal_task = None  # Allow new generation next segment
            else:
                fal_status = "Still running, reusing current pair..."
                print("[FAL] Image not ready yet, reusing current pair")
                # Keep fal_task alive, don't cancel — it'll be checked next segment

        print(f"\nCompleted {elapsed}s of streaming")

    except OdysseyAuthError:
        print("Invalid API key")
    except OdysseyConnectionError as e:
        print(f"Connection failed: {e}")
    except asyncio.CancelledError:
        print("Stream cancelled")
    finally:
        await client.disconnect()
        stream_active = False
        current_prompt = ""
        print("Done")


def start_odyssey_thread():
    try:
        asyncio.run(run_odyssey())
    except Exception as e:
        print(f"Odyssey error: {e}")
        import traceback
        traceback.print_exc()


@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>On the Phone - Live</title>
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
                max-height: 70vh;
                border: 2px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
            }
            .info {
                margin-top: 20px;
                display: flex;
                gap: 12px;
                align-items: center;
            }
            .status {
                padding: 10px 20px;
                background: #333;
                border-radius: 4px;
            }
            .status.active { background: #4CAF50; }
            .pair {
                padding: 10px 20px;
                background: #2a2a2a;
                border-left: 3px solid #e67e22;
                border-radius: 4px;
                font-size: 14px;
                color: #e67e22;
            }
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
            .fal {
                margin-top: 8px;
                padding: 8px 20px;
                background: #2a2a2a;
                border-left: 3px solid #9b59b6;
                border-radius: 4px;
                font-size: 13px;
                color: #9b59b6;
                min-height: 1.2em;
            }
        </style>
    </head>
    <body>
        <h1>On the Phone - Live</h1>
        <img src="/video_feed" alt="Video Stream">
        <div class="info">
            <div class="status" id="status">Waiting for stream...</div>
            <div class="pair" id="pair"></div>
        </div>
        <div class="prompt" id="prompt"></div>
        <div class="fal" id="fal"></div>
        <script>
            setInterval(async () => {
                try {
                    const res = await fetch('/stream_status');
                    const data = await res.json();
                    const el = document.getElementById('status');
                    const promptEl = document.getElementById('prompt');
                    const pairEl = document.getElementById('pair');
                    const falEl = document.getElementById('fal');
                    if (data.active) {
                        el.textContent = 'Stream Active';
                        el.className = 'status active';
                        promptEl.textContent = data.prompt || '';
                        pairEl.textContent = data.pair || '';
                        falEl.textContent = data.fal ? 'FAL: ' + data.fal : '';
                    } else {
                        el.textContent = 'Stream Ended';
                        el.className = 'status';
                        promptEl.textContent = '';
                        pairEl.textContent = '';
                        falEl.textContent = '';
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
    return jsonify({
        "active": stream_active,
        "prompt": current_prompt,
        "pair": current_pair_label,
        "fal": fal_status,
    })


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

    segments = TOTAL_DURATION // SECONDS_PER_SEGMENT
    print("Starting web server at http://127.0.0.1:5001")
    print(f"Running {segments} segments of {SECONDS_PER_SEGMENT}s each, {TOTAL_DURATION}s total")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
    except KeyboardInterrupt:
        should_shutdown = True
        stream_active = False
