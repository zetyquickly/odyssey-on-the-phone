import asyncio
import glob
import os
import sys
import threading

from flask import Flask, render_template_string, jsonify
from odyssey import Odyssey

app = Flask(__name__)

# Configuration
API_KEY = os.environ["API_KEY"]
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "on_the_phone")

# Global state
simulation_status = "starting"
current_step = ""
video_url = None


def get_image():
    """Get the first PNG image from on_the_phone folder."""
    paths = sorted(glob.glob(os.path.join(IMAGE_DIR, "*.png")))
    if not paths:
        print(f"No PNG images found in {IMAGE_DIR}")
        sys.exit(1)
    print(f"Using image: {os.path.basename(paths[0])}")
    return paths[0]


async def run_simulation():
    global simulation_status, current_step, video_url

    client = Odyssey(api_key=API_KEY)
    image_path = get_image()

    script = [
        {
            "timestamp_ms": 0,
            "start": {
                "prompt": "Two people are talking on the phone, one is speaking animatedly while the other listens with interest",
                "image": image_path,
            },
        },
        {
            "timestamp_ms": 10000,
            "interact": {
                "prompt": "One person surprises the other with a gift, both are laughing and reacting with excitement",
            },
        },
        {
            "timestamp_ms": 20000,
            "interact": {
                "prompt": "The person on the right leaves his frame and goes to the person on the left, they hug each other warmly, smiling and happy",
            },
        },
        {"timestamp_ms": 30000, "end": {}},
    ]

    try:
        current_step = "Submitting simulation..."
        simulation_status = "submitting"
        print("Submitting simulation...")
        for entry in script:
            ts = entry["timestamp_ms"] / 1000
            if "start" in entry:
                print(f"  [{ts:5.0f}s] START: {entry['start']['prompt'][:70]}...")
            elif "interact" in entry:
                print(f"  [{ts:5.0f}s] INTERACT: {entry['interact']['prompt'][:70]}...")
            elif "end" in entry:
                print(f"  [{ts:5.0f}s] END")

        job = await client.simulate(script=script, portrait=False)
        print(f"\nSimulation submitted: {job.job_id}")

        simulation_status = "processing"
        current_step = "Processing simulation..."
        print("\nWaiting for completion...")

        while True:
            await asyncio.sleep(5)
            status = await client.get_simulate_status(job.job_id)
            status_str = str(status.status)
            print(f"  Status: {status_str}")

            if "COMPLETED" in status_str.upper():
                print("\nSimulation completed!")
                for stream in status.streams:
                    recording = await client.get_recording(stream.stream_id)
                    video_url = recording.video_url
                    print(f"  Video URL: {video_url}")
                    print(f"  Duration: {recording.duration_seconds}s")
                simulation_status = "completed"
                current_step = "Video ready!"
                break

            if "FAILED" in status_str.upper():
                simulation_status = "failed"
                current_step = f"Failed: {status.error_message}"
                print(f"\nSimulation failed: {status.error_message}")
                break

            if "CANCELLED" in status_str.upper():
                simulation_status = "cancelled"
                current_step = "Simulation was cancelled"
                print("\nSimulation was cancelled")
                break

    except Exception as e:
        simulation_status = "failed"
        current_step = f"Error: {e}"
        print(f"Error: {e}")
    finally:
        await client.disconnect()


def start_simulation_thread():
    try:
        asyncio.run(run_simulation())
    except Exception as e:
        print(f"Simulation thread error: {e}")
        import traceback
        traceback.print_exc()


@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Simulation</title>
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
            video {
                max-width: 90vw;
                max-height: 70vh;
                border: 2px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
                display: none;
            }
            .status {
                margin-top: 20px;
                padding: 10px 20px;
                background: #333;
                border-radius: 4px;
            }
            .status.processing { background: #e67e22; }
            .status.completed { background: #4CAF50; }
            .status.failed { background: #e74c3c; }
            .step {
                margin-top: 12px;
                padding: 10px 20px;
                background: #2a2a2a;
                border-left: 3px solid #4CAF50;
                border-radius: 4px;
                font-size: 14px;
                max-width: 600px;
                color: #ccc;
            }
        </style>
    </head>
    <body>
        <h1>On the Phone - Simulation</h1>
        <video id="video" controls autoplay></video>
        <div class="status" id="status">Starting...</div>
        <div class="step" id="step"></div>
        <script>
            let videoLoaded = false;
            setInterval(async () => {
                try {
                    const res = await fetch('/sim_status');
                    const data = await res.json();
                    const statusEl = document.getElementById('status');
                    const stepEl = document.getElementById('step');
                    const videoEl = document.getElementById('video');

                    stepEl.textContent = data.step || '';
                    statusEl.textContent = data.status.toUpperCase();
                    statusEl.className = 'status ' + data.status;

                    if (data.status === 'completed' && data.video_url && !videoLoaded) {
                        videoEl.src = data.video_url;
                        videoEl.style.display = 'block';
                        videoLoaded = true;
                    }
                } catch (e) {}
            }, 2000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@app.route("/sim_status")
def sim_status():
    return jsonify({
        "status": simulation_status,
        "step": current_step,
        "video_url": video_url,
    })


if __name__ == "__main__":
    sim_thread = threading.Thread(target=start_simulation_thread, daemon=True)
    sim_thread.start()

    print("Starting web server at http://127.0.0.1:5001")
    print("Simulation running in background...")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
    except KeyboardInterrupt:
        pass
