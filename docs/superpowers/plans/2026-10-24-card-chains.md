# Card lock chain animation — 2026-10-24

- Add two decorative crossing SVG chains on existing native card faces; preserve masked data and business controls.
- Observe authoritative normal/locked markers. Animate only successful transitions; initial locked cards remain static.
- Reverse the chain and padlock animation after successful unlock. Preserve focus/return animations and reduced-motion behavior.
- Validate preview and wrong OTP paths with AppTest, animation lifecycle with DOM/browser checks, then run scripts/verify.sh and independent review.
