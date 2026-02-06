"""Test the cooldown mechanism"""

import threading
import time

INTERACTION_COOLDOWN = 3
interaction_ready = True


def _reset_interaction_ready():
    """Reset the interaction ready flag after cooldown"""
    global interaction_ready
    interaction_ready = True
    print("Interaction ready - cooldown complete")


def test_interaction():
    """Simulate an interaction"""
    global interaction_ready

    print(f"Before interaction: interaction_ready = {interaction_ready}")

    # Set cooldown
    interaction_ready = False
    print(f"Interaction cooldown started for {INTERACTION_COOLDOWN} seconds")
    print(f"After setting False: interaction_ready = {interaction_ready}")

    # Start cooldown timer using threading.Timer
    cooldown_timer = threading.Timer(INTERACTION_COOLDOWN, _reset_interaction_ready)
    cooldown_timer.daemon = True
    cooldown_timer.start()
    print(f"Timer started: {cooldown_timer.is_alive()}")

    # Wait and check the status
    for i in range(5):
        time.sleep(1)
        print(f"After {i + 1} second(s): interaction_ready = {interaction_ready}")


if __name__ == "__main__":
    test_interaction()
    print("\nTest complete!")
