"""Print a fresh VAPID key pair for Web Push.

    python -m app.scripts.generate_vapid_keys

Copy the two lines into your .env (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY).
Keep the private key secret; changing the pair invalidates existing subscriptions.
"""
from app.services.notifications import generate_vapid_keys

if __name__ == "__main__":
    k = generate_vapid_keys()
    print(f"VAPID_PUBLIC_KEY={k['public']}")
    print(f"VAPID_PRIVATE_KEY={k['private']}")
