# assets/

This directory contains vendored binary assets shipped with the `voice_commander` package.

## silero_vad.onnx

**Source:** [snakers4/silero-vad](https://github.com/snakers4/silero-vad) on GitHub  
**Version vendored:** silero-vad 6.2.1 (PyPI)  
**License:** MIT — see https://github.com/snakers4/silero-vad/blob/master/LICENSE  
**Copied from:** `silero_vad/data/silero_vad.onnx` inside the silero-vad wheel  

The model is used by `voice_commander.vad_onnx.SileroVADOnnx` — a torch-free
ONNX-runtime wrapper that replaces the `silero-vad` PyPI dependency to eliminate
the transitive torch/torchaudio import (~300-400 MB RSS savings).

Attribution as required by the MIT license:

> Copyright (c) 2021 Silero Team
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
