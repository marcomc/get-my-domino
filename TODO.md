# TODO

## Next Steps

- Improve issue titles extracted from the `my_domino` product listing.
- Evaluate whether a future Domino API exposes renewable auth tokens.
- Add release automation if the project will publish packages or binaries.

## Propositions

- [ ] AI-assisted speech text normalization before audio synthesis

  Description: add an optional pre-audio normalization stage that invokes the
  local `codex` CLI as an external tool, not as an imported library, to produce
  a separate speech-ready text file from each downloaded article. The original
  `.txt` export must remain a faithful readable export; audio should be
  generated from the normalized speech file when the feature is enabled. Do
  not rely on Apple `say` to infer typography, missing accents, foreign
  pronunciation, or extraction-induced pauses correctly; the pipeline must
  preprocess the text before handing it to TTS.

  Label: `idea:ai-speech-normalization`

  Actions:

  - Add config keys and matching CLI flags:
    `speech_normalize_auto`, `speech_normalize_command`,
    `speech_normalize_model`, `speech_normalize_timeout`,
    `speech_normalize_force`, and `--speech-normalize` / `--no-speech-normalize`.
  - Store normalized output beside the article export as
    `<article-basename>.speech.txt`; generate audio from that file when it
    exists and the feature is enabled.
  - Treat `.speech.txt` as the only TTS input when speech normalization is
    enabled. Apple `say` should receive already-normalized plain text, not the
    raw article export.
  - Keep regeneration incremental: reuse an existing `.speech.txt` unless the
    source `.txt` is newer, the speech prompt version changed, or `--force` /
    `speech_normalize_force` is set.
  - Add metadata fields for `speech_text_path`, `speech_normalized_at`,
    `speech_prompt_version`, `speech_normalizer_command`, and
    `speech_source_sha256`.
  - Invoke `codex` via `subprocess` with a prompt file and explicit input/output
    paths; capture stdout/stderr to a local log file without printing article
    contents to the terminal.
  - Fail closed by default: if AI normalization fails, continue with the
    original `.txt` only when an explicit `speech_normalize_fallback = true`
    option is set; otherwise stop before audio generation with a clear error.
  - Draft the prompt so the model preserves meaning and sequence exactly, does
    not summarize, does not rewrite style, and only changes orthography,
    punctuation, line breaks, and pronunciation helpers needed for TTS.
  - Encode pronunciation helpers as plain text that macOS `say` can read
    naturally. Do not output SSML/XML tags unless a future synthesis backend is
    explicitly added and tested for markup support.
  - Require the prompt to repair known Domino/TTS artifacts:
    `(ri)tornava` -> `ritornava`; `transita(va)` -> `transitava` when context
    requires the past tense; isolated one-word lines such as `Jahannam`,
    `Artesh`, `pasdaran`, or `intelligence` must be rejoined with surrounding
    prose when they are not real headings.
  - Require the prompt to convert parenthetical dash inserts that would be read
    awkwardly, for example `– si fa per dire –` -> `(si fa per dire),` when the
    surrounding syntax needs a comma after the insert.
  - Require the prompt to normalize paragraph breaks conservatively: keep real
    paragraph boundaries, but remove line breaks that split a sentence after an
    article, preposition, adjective, quotation, or isolated foreign term.
  - Require the prompt to restore Italian pronunciation marks only when the
    context strongly disambiguates the word; never add accents speculatively,
    and leave uncertain cases unchanged. Examples to handle only when context
    is clear include ambiguous pairs such as `principi` / `princìpi`,
    `ancora` / `ancóra`, `subito` / `subìto`, and similar Italian homographs
    whose missing stress can mislead TTS.
  - Define the accent policy as pronunciation disambiguation, not copyediting:
    add a written accent only when the same spelling can be read with multiple
    stresses and the article context selects one meaning. For example,
    `i principi della geopolitica` should become `i princìpi della geopolitica`,
    while `i principi sauditi` should become `i prìncipi sauditi`.
  - Require the prompt to preserve foreign names, transliterations, original
    scripts, and geopolitically meaningful terms; it may add pronunciation
    aids only in plain text forms that `say` can read naturally.
  - Require the prompt to identify foreign or loan expressions that Apple
    Italian TTS is likely to misread (`think tank`, `regime change`,
    `Washington Post`, `New York Times`, `b movie`, `social media`, and similar)
    and apply only conservative plain-text pronunciation fixes that do not
    alter meaning or visual identity beyond the speech-ready file.
  - Add a deterministic pre-pass before the AI call for safe mechanical fixes:
    Unicode whitespace cleanup, repeated blank-line collapse inside sentences,
    orphan punctuation repair, and removal of source URLs from legacy text.
  - Add a review command, for example
    `get-my-domino speech-normalize <article-dir> --diff`, that produces the
    `.speech.txt` file and prints a unified diff for human approval/testing.
  - Add tests with fixture snippets from issue `2026-04`: `Jahannam` isolated by
    blank lines, `– si fa per dire –`, `(ri)tornava`, `transita(va)`,
    `Donald(o)`, split `ʿ / Alī Khāmeneī`, and split `intelligence / nazionale`.
  - Document privacy and reproducibility: the `codex` CLI may send article text
    to the configured AI service; users must explicitly enable the feature and
    can keep the current fully local pipeline by leaving it disabled.

  Initial prompt draft:

  ```text
  You are preparing an Italian geopolitics article for macOS text-to-speech.
  Read the input UTF-8 text file and write only the corrected speech-ready text
  to the requested output file.

  The output will be read by Apple `say` as plain text. Do not assume the TTS
  engine will correctly infer missing stress marks, typographic wordplay,
  foreign pronunciation, or layout-induced pauses. Preprocess those cases in
  this file before synthesis. Do not emit SSML, XML, markdown, comments, or
  explanatory notes.

  Preserve the article's meaning, facts, order, authorial style, and wording.
  Do not summarize, translate, modernize vocabulary, simplify syntax, or add new
  content. Only make minimal orthographic, punctuation, spacing, line-break, and
  pronunciation-oriented changes needed to make a TTS engine read naturally.

  Fix typographic wordplay that harms pronunciation when the intended spoken
  word is clear: "(ri)tornava" becomes "ritornava"; "transita(va)" becomes the
  contextually correct spoken form, normally "transitava" in past-tense prose.
  Keep true parenthetical content when it changes meaning.

  Repair line breaks created by page extraction. If a single word or short
  foreign expression is isolated between blank lines but syntactically belongs
  to the surrounding sentence, join it back into that sentence and adjust
  punctuation. Example: "prossima al\n\nJahannam\n\n. Peggio" becomes
  "prossima al Jahannam.\nPeggio".

  Convert dash inserts that sound unnatural in TTS into smoother punctuation
  only when the syntax remains equivalent. Example:
  "nei nostri pensatoi – si fa per dire – per cui" becomes
  "nei nostri pensatoi (si fa per dire), per cui".

  Restore Italian accents only when context makes the pronunciation
  unambiguous, including homographs where the stress changes the spoken word
  and meaning, such as "principi/princìpi", "ancora/ancóra", "subito/subìto",
  or comparable cases. Do not guess: if context is not strong enough, leave the
  word unchanged.

  Treat this as pronunciation disambiguation, not copyediting. Add a written
  accent only when the same spelling can be pronounced in more than one way and
  the local sentence selects one meaning. Example: "i principi della
  geopolitica" should become "i princìpi della geopolitica"; "i principi
  sauditi" should become "i prìncipi sauditi".

  Preserve all foreign names, transliterations, original scripts, and
  geopolitically meaningful terms. For foreign or loan expressions that Italian
  TTS is likely to pronounce mechanically, apply only conservative plain-text
  pronunciation aids that remain readable and do not change the article's
  meaning. Output only the final speech-ready text.
  ```
