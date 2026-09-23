# Screens with no tree

Where the accessibility tree runs out, and why saying so beats returning a screen that merely looks empty.

- **Some screens have no tree to capture at any price.** A Flutter view is one
  canvas with no accessible children, and web content is a second tree behind a
  context switch this stack has none of. The digest carries a `note` saying so
  and naming `ios_screenshot`, because the alternative is a screen that merely
  looks empty. See `docs/adr/0007`.
