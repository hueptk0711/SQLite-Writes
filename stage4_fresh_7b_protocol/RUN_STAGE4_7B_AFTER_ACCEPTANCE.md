# Run Stage 4 Fresh 7B After Reviewer Acceptance

Target server path requested by user:

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk
```

Upload the accepted code/package from the local machine:

```powershell
scp "D:\paper kltn\text to sql\reviewer_packages\Stage4_FRESH_7B_PROTOCOL_REVIEWER_PACKAGE_20260822.zip" uet@222.255.250.24:/home/uet/hue_ptk/
```

On the server, unpack only after protocol acceptance:

```bash
cd /home/uet/hue_ptk
unzip Stage4_FRESH_7B_PROTOCOL_REVIEWER_PACKAGE_20260822.zip -d Stage4_FRESH_7B_PROTOCOL_REVIEW
```

Before any model generation, run the GPU/tokenizer prompt-length preflight using
the accepted local Qwen2.5-Coder-7B-Instruct snapshot. If any prompt overflows,
stop and report; do not truncate ad hoc.
