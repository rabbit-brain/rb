# Rabbit Brain

Rabbit Brain keeps a record of the experiments your AI coding agent runs. Results come from the runs themselves, not from the agent's summary.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/rabbit-brain/rb/main/docs/images/rb-open-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/rabbit-brain/rb/main/docs/images/rb-open-light.png">
  <img alt="rb open in the browser, showing the record of a sample project" src="https://raw.githubusercontent.com/rabbit-brain/rb/main/docs/images/rb-open-light.png" width="1280">
</picture>

## Quick start: a sample study

No setup and no research of your own needed. Open Claude Code or Codex in an empty folder and paste this:

```
Clone https://github.com/rabbit-brain/think-step-by-step and work inside it.
Install rabbit-brain from PyPI into a virtual environment in that folder. It's open source: https://github.com/rabbit-brain/rb
Start rb login in the background and show me the sign-in link it prints. I'll sign in.
When I'm signed in, run rb sync and give me the link it prints.
Run rb context and tell me in three lines what the study found and what's still open.
Don't run any experiments.
```

Your agent gives you a sign-in link (an email code, no password). Then the study opens in your private account on rabbitbrain.ai. No account? Ask your agent to run rb page instead.

## Use it on your own project

```sh
pip install rabbit-brain
rb init "My project"
```

Then paste this into your agent:

```
Use Rabbit Brain to track this: read rb context, then record where the work stands.
```

No agent? Run `uvx --from rabbit-brain rb tour` for a guided example in your terminal.

Usage analytics are off until you run `rb telemetry enable` ([PRIVACY.md](https://github.com/rabbit-brain/rb/blob/main/PRIVACY.md)).

[Docs](https://rabbitbrain.ai/docs) · License: [Apache-2.0](https://github.com/rabbit-brain/rb/blob/main/LICENSE)
