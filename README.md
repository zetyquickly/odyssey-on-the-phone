# Odyssey Demos

## Setup

```bash
export API_KEY="your_odyssey_api_key"
export FAL_KEY="your_fal_api_key"
```

## How to run

```bash
uv run python interact_longer_scenario.py
```

Then open http://127.0.0.1:5001

## What is this

Two AI models working together to generate an infinite conversation between pairs of people.

**Odyssey** streams real-time video from a static image — animating two people talking, reacting, and interacting. **Fal (Flux)** generates the next combined image by compositing a new pair of people from individual portraits.

While one pair is being streamed live, the next pair's image is already being generated in the background. When the segment ends, a push transition with blended overlap slides the departing person out and the newcomer in, keeping the shared person in place. Then the next stream begins — and so on, indefinitely.
