# TODO

## Next Steps

- Improve issue titles extracted from the `my_domino` product listing.
- Evaluate whether a future Domino API exposes renewable auth tokens.
- Add release automation if the project will publish packages or binaries.
- Add speech-normalization metadata to `metadata.json`, including prompt file
  path, prompt version, normalizer command, source hash, and normalization time.
- Consider invalidating existing `.speech.txt` files when the configured prompt
  file changes.

## Propositions

- [ ] Evaluate future non-Codex speech-normalization backends

  The speech-normalization configuration is already structured for multiple AI
  agents, but only the local `codex exec` backend is implemented and tested.
  Future candidates include Codex Cloud, GitHub CLI, GitHub Copilot CLI, and
  Jelly.
