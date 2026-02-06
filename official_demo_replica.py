import asyncio
import os
import signal
import sys
import threading
import time
from io import BytesIO
from queue import Queue
from flask import Flask, Response, render_template_string, request, jsonify
from PIL import Image
from odyssey import Odyssey, OdysseyAuthError, OdysseyConnectionError

app = Flask(__name__)

# Global variables for frame sharing
current_frame = None
frame_lock = threading.Lock()
stream_active = False
odyssey_client = None
should_shutdown = False
last_interaction_time = None

# Interaction queue
interaction_queue = Queue()

# Stream configuration
STREAM_TIMEOUT = 30  # seconds after last interaction before ending stream
INTERACTION_COOLDOWN = 3  # seconds to wait between interactions
interaction_ready = True


def save_frame(frame):
    """Callback to save the latest frame"""
    global current_frame
    with frame_lock:
        current_frame = frame


def generate_frames():
    """Generator function to yield frames as MJPEG"""
    global current_frame, stream_active
    import time

    while True:
        with frame_lock:
            if current_frame is not None:
                # VideoFrame.data is already an RGB numpy array
                img = Image.fromarray(current_frame.data)

                # Convert to JPEG bytes
                buffer = BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                frame_bytes = buffer.getvalue()

                # Yield frame in MJPEG format
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                )

        # Small delay to control frame rate
        time.sleep(0.033)  # ~30 fps


API_KEY = os.environ["API_KEY"]


async def run_odyssey():
    """Run the Odyssey client"""
    print("[DEBUG] run_odyssey() called")
    global \
        stream_active, \
        odyssey_client, \
        should_shutdown, \
        last_interaction_time, \
        interaction_ready
    print("[DEBUG] Creating Odyssey client")
    odyssey_client = Odyssey(api_key=API_KEY)
    try:
        print("[DEBUG] Calling connect()...")
        await odyssey_client.connect(
            on_video_frame=save_frame,
            on_stream_started=lambda stream_id: print(f"Ready: {stream_id}"),
        )
        print("[DEBUG] Connected successfully")
        stream_active = True
        print("Stream starting...")
        await odyssey_client.start_stream("A cat", portrait=True)

        # Initial interaction is not ready until after first automated interaction
        interaction_ready = False

        await asyncio.sleep(3)  # Wait 3 seconds
        await odyssey_client.interact("Pet the cat")

        # Update last interaction time
        last_interaction_time = time.time()

        # Wait for cooldown before allowing user interactions
        await asyncio.sleep(INTERACTION_COOLDOWN)
        interaction_ready = True
        print("Ready for user interactions")

        # Keep stream alive until timeout or shutdown
        while stream_active and not should_shutdown:
            # Check for queued interactions
            if not interaction_queue.empty():
                prompt = interaction_queue.get()
                print(f"[DEBUG] Processing queued interaction: {prompt}")
                try:
                    await odyssey_client.interact(prompt)
                    print(f"[DEBUG] Interaction completed successfully: {prompt}")
                    last_interaction_time = time.time()
                except Exception as e:
                    print(f"[DEBUG] Error processing interaction: {e}")
                    import traceback

                    traceback.print_exc()

            await asyncio.sleep(0.1)  # Check queue frequently

            # Check if timeout has been reached
            if last_interaction_time is not None:
                elapsed = time.time() - last_interaction_time
                if elapsed > STREAM_TIMEOUT:
                    print(
                        f"Stream timeout after {STREAM_TIMEOUT} seconds of inactivity"
                    )
                    break

        # End stream gracefully
        print("Ending stream...")
        await odyssey_client.end_stream()

    except OdysseyAuthError:
        print("Invalid API key")
    except OdysseyConnectionError as e:
        print(f"Connection failed: {e}")
    except asyncio.CancelledError:
        print("Stream cancelled")
    finally:
        if odyssey_client:
            await odyssey_client.disconnect()
        stream_active = False
        print("Stream ended")


def start_odyssey_thread():
    """Start Odyssey in a separate thread"""
    print("[DEBUG] Odyssey thread started")
    try:
        asyncio.run(run_odyssey())
    except Exception as e:
        print(f"[DEBUG] Exception in Odyssey thread: {e}")
        import traceback
        traceback.print_exc()


@app.route("/")
def index():
    """Main page with video player"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Odyssey Video Stream</title>
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
            h1 {
                margin-bottom: 20px;
            }
            .video-container {
                display: flex;
                flex-direction: column;
                align-items: center;
            }
            img {
                max-width: 90vw;
                max-height: 70vh;
                border: 2px solid #333;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
            }
            .controls {
                margin-top: 20px;
                display: flex;
                gap: 10px;
                align-items: center;
            }
            input[type="text"] {
                padding: 10px 15px;
                font-size: 16px;
                border: 2px solid #333;
                border-radius: 4px;
                background: #2a2a2a;
                color: white;
                min-width: 300px;
            }
            input[type="text"]:disabled {
                background: #1a1a1a;
                color: #666;
                cursor: not-allowed;
            }
            button {
                padding: 10px 20px;
                font-size: 16px;
                background: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                transition: background 0.3s;
            }
            button:hover:not(:disabled) {
                background: #45a049;
            }
            button:disabled {
                background: #666;
                cursor: not-allowed;
            }
            .status {
                margin-top: 20px;
                padding: 10px 20px;
                background: #333;
                border-radius: 4px;
            }
            .status.active {
                background: #4CAF50;
            }
        </style>
    </head>
    <body>
        <h1>Odyssey Video Stream</h1>

        <div class="video-container">
            <img src="/video_feed" alt="Video Stream">

            <div class="controls">
                <input
                    type="text"
                    id="interactInput"
                    placeholder="Enter interaction (e.g., 'Make it jump')"
                    disabled
                >
                <button id="interactBtn" onclick="sendInteract()" disabled>
                    Interact
                </button>
            </div>

            <div class="status" id="status">Waiting for stream...</div>
        </div>

        <script>
            let streamActive = false;
            let interactionReady = true;

            // Check stream and interaction status periodically
            async function checkStatus() {
                try {
                    // Check stream status
                    const streamResponse = await fetch('/stream_status');
                    const streamData = await streamResponse.json();
                    streamActive = streamData.active;

                    // Check interaction ready status
                    const readyResponse = await fetch('/interaction_ready');
                    const readyData = await readyResponse.json();
                    interactionReady = readyData.ready;

                    // Update UI
                    const status = document.getElementById('status');
                    const interactInput = document.getElementById('interactInput');
                    const interactBtn = document.getElementById('interactBtn');

                    if (streamActive) {
                        if (interactionReady) {
                            status.textContent = 'Stream Active - Ready for interaction';
                            status.className = 'status active';
                            interactInput.disabled = false;
                            interactBtn.disabled = false;
                            interactBtn.textContent = 'Interact';
                        } else {
                            status.textContent = 'Stream Active - Processing...';
                            status.className = 'status active';
                            interactInput.disabled = true;
                            interactBtn.disabled = true;
                            interactBtn.textContent = 'Processing...';
                        }
                    } else {
                        status.textContent = 'Stream Ended';
                        status.className = 'status';
                        interactInput.disabled = true;
                        interactBtn.disabled = true;
                    }
                } catch (error) {
                    console.error('Error checking status:', error);
                }
            }

            // Send interact command
            async function sendInteract() {
                const input = document.getElementById('interactInput');
                const btn = document.getElementById('interactBtn');
                const prompt = input.value.trim();

                if (!prompt) {
                    alert('Please enter an interaction');
                    return;
                }

                // Immediately disable controls
                btn.disabled = true;
                input.disabled = true;
                btn.textContent = 'Sending...';

                try {
                    const response = await fetch('/interact', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ prompt: prompt })
                    });

                    const result = await response.json();

                    if (response.ok) {
                        input.value = '';
                        btn.textContent = 'Processing...';
                        // Controls will be re-enabled by checkStatus() when ready
                    } else {
                        alert('Error: ' + result.error);
                        btn.textContent = 'Interact';
                        btn.disabled = false;
                        input.disabled = false;
                    }
                } catch (error) {
                    alert('Error: ' + error.message);
                    btn.textContent = 'Interact';
                    btn.disabled = false;
                    input.disabled = false;
                }
            }

            // Allow Enter key to send interact
            document.getElementById('interactInput').addEventListener('keypress', function(e) {
                if (e.key === 'Enter' && !e.target.disabled) {
                    sendInteract();
                }
            });

            // Check status every 500ms for responsive updates
            setInterval(checkStatus, 500);
            checkStatus(); // Initial check
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@app.route("/stream_status")
def stream_status():
    """API endpoint to check if stream is active"""
    return jsonify({"active": stream_active})


def _reset_interaction_ready():
    """Reset the interaction ready flag after cooldown"""
    global interaction_ready
    interaction_ready = True
    print("Interaction ready - cooldown complete")


def _send_interaction_async(prompt):
    """Queue interaction to be processed by main Odyssey event loop"""
    print(f"[DEBUG] Queueing interaction: {prompt}")

    # Put the prompt in the queue for the main Odyssey loop to process
    interaction_queue.put(prompt)
    print("[DEBUG] Interaction queued successfully")

    # Start cooldown timer using threading.Timer
    cooldown_timer = threading.Timer(INTERACTION_COOLDOWN, _reset_interaction_ready)
    cooldown_timer.daemon = True
    cooldown_timer.start()
    print(f"[DEBUG] Cooldown timer started, will reset in {INTERACTION_COOLDOWN}s")


@app.route("/interact", methods=["POST"])
def interact():
    """API endpoint to send an interaction"""
    global odyssey_client, stream_active, last_interaction_time, interaction_ready

    if not stream_active:
        return jsonify({"error": "Stream not active"}), 400

    if not interaction_ready:
        return jsonify({"error": "Please wait before sending another interaction"}), 429

    if odyssey_client is None:
        return jsonify({"error": "Client not initialized"}), 500

    data = request.json
    prompt = data.get("prompt", "")

    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400

    # Set cooldown immediately
    interaction_ready = False
    print(f"Interaction cooldown started for {INTERACTION_COOLDOWN} seconds")
    print(f"interaction_ready set to: {interaction_ready}")

    # Run interaction in background thread to avoid blocking
    interaction_thread = threading.Thread(
        target=_send_interaction_async, args=(prompt,), daemon=True
    )
    interaction_thread.start()

    return jsonify({"success": True, "message": "Interaction queued"})


@app.route("/interaction_ready")
def check_interaction_ready():
    """API endpoint to check if ready for new interaction"""
    return jsonify({"ready": interaction_ready})


@app.route("/video_feed")
def video_feed():
    """Video streaming route"""
    return Response(
        generate_frames(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    global should_shutdown, stream_active
    print("\nReceived interrupt signal, shutting down...")
    should_shutdown = True
    stream_active = False
    sys.exit(0)


if __name__ == "__main__":
    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

    # Start Odyssey in a background thread
    odyssey_thread = threading.Thread(target=start_odyssey_thread, daemon=True)
    odyssey_thread.start()

    # Start Flask server
    print("Starting web server at http://127.0.0.1:5001")
    print(f"Stream will timeout after {STREAM_TIMEOUT} seconds of inactivity")
    print("Press Ctrl+C to stop")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        should_shutdown = True
        stream_active = False
