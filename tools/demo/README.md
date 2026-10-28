# The demo's study: int8-cifar

The product demo brings a real research project into rb. This folder makes that project.

- `study/` is the project's code: ResNet-20 on CIFAR-10 trained in FP32 (`train.py`), post-training INT8 with fbgemm at per-tensor or per-channel weight scales (`quantize.py`), `eval.py`, a sweep that keeps one row per run in `results/runs.db` (`sweep.py`), and `configs/*.yaml`.
- `make_study.py` builds the repository an agent leaves after two weeks of that study: it runs every training, quantization and evaluation for real, at a scenario's dates (Oct 5 to 16, 2026), and commits as the researcher (ana, made up). It leaves 31 run folders, one of them a run that was killed and then rerun, notes.md with a decision in the researcher's words, a dated history in which `configs/data.yaml`'s normalization changes after the first runs, the agent's log in `logs/agent.log`, and the next sweep set up in `configs/sweep.yaml`, not run and not committed.

Build it on the Linux box (about two hours on 8 cores):

```
python tools/demo/make_study.py --out /srv/rb/demo/int8-cifar --data /srv/rb/demo/cache/cifar10 --threads 8 --cores 8-15
python tests/test_demo_record.py /srv/rb/demo/int8-cifar
```

`--data` holds torchvision's `cifar-10-batches-py` (MD5 of the archive: c58f30108f718f92721af3b95e74349a). The second line checks that the build holds what the demo plan lists; `tests/test_demo_record.py` makes the same checks on a build with `--tiny` (random images, a short schedule, about two minutes). Python needs PyTorch CPU, torchvision and PyYAML.

## The import

`import_take.py` runs one take of the import (SPRINT-OCT24 6.1): it copies the study to a take folder and, in a pty with a clean terminal (no agent-session variable of whatever starts it), plays the researcher: `rb init`, then a Claude Code session given the researcher's two-line brief and the line rb init prints for the agent's chat. Everything the pty prints is kept with its time as an asciinema v2 cast (`import.cast`) for the time-lapse; the take's `.rb/` is what rb wrote. The agent's screen runs with Claude Code's diff of changed files off (it would show `.rb/log.jsonl`, whose lines name the writer and the session) and with no update mid-take.

```
python tools/demo/import_take.py --study /srv/rb/demo/int8-cifar --take /srv/rb/demo/takes/6 \
    --rb-bin <dir holding rb> --study-bin <dir holding the study's python>
```

Run it in tmux; a take takes about ten minutes. The researcher's own steps afterwards (the reference check, the confirmation of the notes.md decision, the commit) are rb commands run in a clean terminal in the take's folder; the box's out/RESUME-D2.md lists the ones the demo record holds.

## The capture and the cut

- `term_take.py` records a terminal beat for real (the commands typed into bash in a pty, kept as a cast).
- `term.html` plays a cast in xterm.js (5.5.0 from cdnjs, pinned by its hash): `speed` makes a time-lapse, labelled with its speed and the session's own clock (`lapse_from` plays the cast at its own speed until then, so a paste can be read, and the time-lapse only after); `pace` lets a long output scroll line by line; `caption` puts the beat's line under the window.
- `walk.mjs` drives Chrome over CDP: a screencast at 1440 x 900 CSS pixels at device scale 2, every frame kept with its time, real clicks at an element's centre (by CSS or by its own words) and real typing. The cursor exists only in the capture: the walk keeps where its own mouse events put it, and `compose.py` draws it.
- `compose.py` turns a walk into constant 30 fps H.264 and puts two videos side by side; `contact.py` makes a contact sheet of a walk's markers or of a video.
- `film.json` is the film (FILM-V2): prose turn after turn until you're lost, then one living view of where you stand. Its beats are segments, each with its beat, its caption, its voice line and its steps; beats 5 and 8 are two segments each, the line split where the screen moves. Every segment's `caption` is burnt in at the bottom, white on a dark band (50 px at 1080p), so the muted film tells the story; the pitch's `title` puts the wordmark over the Overview, blurred (`blur`). `log.html` shows the study's own `logs/agent.log` at a size a viewer reads: `beat=turns` scrolls from user turn to user turn, lighting each with a turn counter, and `beat=summary` opens on a turn and fills the window with its reply (lines numbered as `sed -n` numbers them), the lines around it faded. cut.py copies the record's `logs/agent.log` beside the casts for it. `cut.py` makes the film by default.
- `cut.json` is dry-run-9's storyboard as segments (`--spec tools/demo/cut.json`, with `--pain-cast`), each with its beat, its voice line and its steps (for rb open as it is, `rb_open_today`, and for the shell, `shell`). `cut.py` captures every segment, fits each to its voice line, joins them and writes the cut, its timing and a contact sheet:

```
python tools/demo/cut.py --record <demo record's folder> --import-cast <take>/import.cast \
    --rb-bin <dir holding rb> --out <dir> --voice <folder of WAVs, or synth> --work ~/demo-cut --share-segment <share dir>
```

It needs node 22, Google Chrome, ffmpeg and a Python with Pillow (and pexpect and pyte for the takes). The record is copied into `--work` before rb open serves it, since a beat may write to it, and rb open's place line shows that path; cut.py refuses a `--work` that holds the record itself and replaces only a copy it made. `--voice synth` speaks each line with ffmpeg's flite, a placeholder for timing only.

Each beat frames the part its voice speaks of: a segment's `zoom` in `film.json` (or `cut.json`) is a box in the walk's CSS pixels (the frame's shape, inside the 1440 x 900 frame, never under 720 wide, so 1.4 to 2 times), and `compose.py` crops the real frames to it and scales them up, drawing the walk's cursor before the crop. The zoom eases in over 0.4 s to be full when the voice starts and eases out only after the line has ended, so it never moves mid-sentence; a cursor outside the box while it is on, or the part the voice speaks of (a `{"mark": "subject", "rect": ...}` step) drawn outside it or under the segment's caption band, stops the cut with what to move. The import's terminal frames the window's text, its time-lapse label in the window's bar (`lapse=bar`).

`--mode shell` (the default) captures the shell, every browser beat zoomed: rb open as the researcher starts it, whose `/` is the shell (no test switch). Each shell segment first marks the shell's pointer guide as seen and loads the page again, in steps marked `lead` that walk.mjs runs before it starts filming, so no frame shows the guide (it shows on a first visit, and every segment is a fresh browser). `--mode rb_open_today` captures rb open as it was before the shell, unzoomed (it needs an rb built before rb open drew the shell). Each segment's voice starts at its `voice` marker, once its screen shows what the line says.

## The share beat (on the Mac)

The share beat shows a real hosted link: `rb sync --watch` in a terminal, a new run recorded, and the record's share link in a fresh browser (a profile of its own, never signed in) showing the new run once the watch has sent it. The box has no account on the hosted service, so it is captured on the Mac:

```
cd <share kit>/int8-cifar && <rb binary> login && <rb binary> sync      # once: the record on the site, privately
# on the record's page on the site, make its share link; then, from this repository:
tools/demo/share_beat.sh <rb binary> <share kit>/int8-cifar <the record's share link> <out dir>
```

- The share kit is the demo record with one more real run's folder in it, not yet recorded (`results/r032_int8_pc_c128_s1`, per-channel at 128 images on seed 1, run on the box); the script's default command records that folder with `rb run add`. A fifth argument gives another command; options after it go to `share_beat.mjs` (`--until '<words>'`, `--window <s>`, `--chrome <path>`).
- It syncs once before filming (so the link shows the record as it stands), then types `rb sync --watch &` and the run's command into the filmed terminal, waits for the watch's next upload, and gives the page 20 s to show the new run by itself before reloading the whole page as a reader would (`segment.json` says which). The link is filmed as a reader opens it, `/s/<link>` itself: the hosted service's bar over rb's page in a sandboxed frame. Every check of the words reads them inside that frame (`segment.json`'s `read_in` says where: `frame process` when Chrome gives the frame a process of its own, `frame world` when it shares the page's). The page must say "<N> recorded runs" before the beat, as rb's page header counts them, and come to show "<N + 1> recorded runs". The page is laid out as a desktop browser lays it out, 1440 CSS px wide (rb draws navigator | working view | inspector from 1280), and scaled into its 1200 x 1200 slot beside the terminal, which keeps its size; the frame must draw the inspector before the beat and after it, or nothing is made (`segment.json`'s `layout` keeps what was read).
- The credential is the one `rb login` keeps on the Mac; the commands run as human:ana, the demo record's researcher, with no agent-session variable.
- It writes `screen.mp4` (1920 x 1200, 30 fps: the terminal beside the page, one clock, a time-lapse labelled with its speed), `screen.marks.jsonl` and `segment.json` into the out dir. `cut.py --share-segment <that dir>` puts it in the cut; without it the share segment is a placeholder card.

On the box it runs against a local stand-in of the hosted service (`share_standin.py`: rb sync's hub calls and the share link, served as the hosted service serves it: as a page, the bar and the frame, with the same sandbox; as the frame's own request, the latest upload's page, and for a page that names rb's shell script (`data-shell`) the host's copy of that script inline, allowed by its hash alone, as HOSTED-VIEW.md has the hub do, so the frame draws the three panes), as `tests/test_demo_record.py` does.

## The agent beats (the math film's agent and fresh)

While an agent works in a terminal, rb open's page updates itself. One take films both on one clock, on the box:

```
node tools/demo/agent_beat.mjs --rb <rb> --record <the math record> --out <take dir> --work ~/agent-take \
  --task "Run worked examples with the answer-tag checker on seeds 0, 1 and 2, the same way as the other runs, and record each with rb." \
  --runs 3 --python <a python with pexpect, pyte and Pillow> --study-bin <the study's venv bin> --cpus 8-15
python tools/demo/agent_cut.py <take dir> --out <cuts>/agent-dry
```

- The record is copied to `--work` (a short path: Claude Code's banner shows it) and everything runs on the copy.
- rb open runs on the copy as human:ana; Chrome films its Overview at 1440 CSS px across the 1920 x 1200 frame from
  before the agent is told anything until the fresh session has answered. Each live swap is a `change` marker with
  the words it newly shows and their boxes (walk.mjs's watch step).
- The agent session (agent_take.py) is told the one line and chooses its commands; the driver types nothing else.
  It ends once the record holds `--runs` more runs and the agent has gone quiet. Then a fresh session is asked
  "Where does this research stand?".
- agent_cut.py writes `agent/` and `fresh/` as cut.py keeps a captured segment (screen.mp4, screen.marks.jsonl,
  segment.json) and `sheet/` (one frame per moment). Each moment plays at 1x; the jumps between them carry a clock
  ("+12 min"). The terminal floats bottom right at 36% of the frame's width and moves, for one moment, to the first
  corner clear of every box that moment's changes draw; segment.json keeps each window and box, and a moment no corner
  clears is refused. Ids shaped like a UUID are masked in the terminal, and its bar says so.

## The voice

The owner records the voice once (SPRINT-OCT24 6.4), about two minutes, in a quiet room, with a USB microphone or a phone:

For the film (`film.json`), read the lines in `out/cuts/dry-run-10/VOICE.md` (each segment's `voice`), one WAV per segment named for its `id`: `pitch.wav`, `turns.wav`, `summary.wav`, `import.wav`, `living.wav`, `rows.wav`, `oneseed.wav`, `baseline.wav`, `goalposts.wav`, `holds.wav`, `receipt.wav`, `call.wav`, `share.wav`, `end.wav` (`rows` and `oneseed`, and `holds` and `receipt`, are the two halves of one sentence). Then `cut.py ... --voice <that folder> --share-segment <share dir>`. For dry-run-9's storyboard (`cut.json`):

1. Read each segment's line from `cut.json` (`voice`), in your own words if you prefer; every number you say must be the one on the screen in that segment. Where a segment has one line per mode (the standing, the receipt, the call), read the line of the mode you will cut: on rb open as it is rb gives the command; in the shell you choose Investigate, type the reason, and rb gives the command that records it.
2. Record each segment as its own take, two or three takes each, as WAV (48 kHz, mono or stereo). Keep the best of each in one folder, named for its segment's `id` in `cut.json`: `pain.wav`, `import.wav`, `standing.wav`, `integrity.wav`, `queue.wav`, `holds.wav`, `perchannel.wav`, `receipt.wav`, `call.wav`, `share.wav` and `end.wav` for the shell (rb open as it was also asks for `reference.wav` and `waiting.wav`; cut.py asks only for the segments that have steps in the mode, and checks each take before it captures anything). Since Overview v2 the shell's payoff opens on the standing card's line, then the evidence integrity strip, then the queue, whose first item (the baseline that does not reproduce) is where the story turns; the call is made from that row's Review button.
3. Make the cut from the kit build, in one command: `cut.py ... --mode <the mode> --voice <that folder>`. Each segment lasts as long as its voice line and a breath, holding its last frame, or as long as its screen when that is longer. `cut.timing.json` gives each beat's start and length, and `sheet.png` a frame every four seconds to check against the voice.

## The onboarding loops

`loops.py` makes three short animations for onboarding, each 5 to 8 seconds, seamless (its last half second dissolves into its first frame), with no voice and one caption line on a band under the screen, so it covers nothing rb shows:

- `import`: the import's cast (`import.cast` of the demo take) as a time-lapse labelled with its speed and the session's own clock, the agent recording the project with rb's commands;
- `panes`: the three panes of rb open: a click on a finding, the inspector showing the runs it rests on, then, from the inquiry's rail, its experiment's Reproduce (what it takes to run them again, and the recipe). It needs the inspector's cards in the rb it runs, and makes no loop of a Reproduce drawn too narrow to read;
- `share`: `rb sync --watch` in a terminal beside rb open's live page. The next sweep's first cell (`configs/sweep.yaml`, per-channel at 128 images, seed 1) is run for real under `rb journal run`, recorded with `rb run add`, the page shows the new reading, and the watch sends it. There is no hub here: the watch sends to a local stand-in that takes the upload and answers with a link on 127.0.0.1, which the terminal shows as it is. Both panes are recorded in real time on one clock and played together as a time-lapse, its speed in the caption.

```
python tools/demo/loops.py --record /srv/rb/demo/record-take4/int8-cifar --import-cast /srv/rb/demo/takes/4/import.cast \
    --rb-bin <dir holding rb> --study-bin <dir holding the study's python> --out <dir> --work ~/lw
```

Each loop is written as `<name>.webm` (VP9) and `<name>.mp4` (H.264) at 1280 wide, `<name>.gif` at 800 wide under 3 MB (fewer frames a second, then fewer colours, until it fits), and `<name>.png`, a poster frame; `loops.json` says each one's seconds, sizes and caption. `--only import,panes,share` makes some of them; `--shell auto` (the default) runs rb open as the researcher does, and with RB_TEST_SHELL=1 only when its own `/` draws no shell (an rb from before the switch). Keep `--work` short: rb open's top bar shows its path.
