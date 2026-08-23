# CPU runtime dependency record

## Decision

HIVE's Compose backend is a CPU deployment: it does not expose a GPU and the
deployed runtime reports `torch.cuda.is_available() == false`. GLiNER requires
Torch, but the default Linux resolution previously selected a CUDA-enabled
wheel and its NVIDIA runtime dependencies.

The project now declares Torch directly, pins `2.12.1`, and routes it through
an explicit `pytorch-cpu` source in `pyproject.toml`. The source follows uv's
documented CPU-only PyTorch configuration:

- <https://docs.astral.sh/uv/guides/integration/pytorch/>
- <https://download.pytorch.org/whl/cpu>

The index is `explicit = true`, so only Torch is resolved from it; ordinary
dependencies continue to use PyPI. `uv.lock` records platform-specific CPU
wheels for reproducible Windows development and Linux container deployment.

## Verified result (23 August 2026)

| Check | Before | CPU-only build |
| --- | ---: | ---: |
| Backend image size | 3,337,709,323 bytes (3.34 GB decimal) | 789,796,786 bytes (790 MB decimal) |
| Reduction | - | 76.3% |
| Deployed Torch | `2.12.1+cu130` | `2.12.1+cpu` |
| CUDA build metadata | `13.0` | `None` |
| CUDA available | `false` | `false` |
| Locked CUDA/NVIDIA packages | 18 plus Triton | 0 |
| Python regression | - | 352 passed, 1 skipped, 1 known deprecation warning |

The optimized live container also completed the real cached
`urchade/gliner_multi-v2.1` load in 19.50 seconds, reported case-intelligence
ready, connected the Telegram data plane, started control-bot polling, and
reported all runtime components ready in 24.95 seconds. This verifies model
operation, not merely module import.

One cold dependency/image build observed during this change completed in about
two minutes rather than about seven minutes for the CUDA-bearing image. Treat
that as an operational observation, not a controlled performance result,
because registry/cache and host-I/O conditions were not frozen.

## Upgrade rule

When changing GLiNER or Torch:

1. Keep the CPU index explicit unless the deployment is deliberately changed
   to expose and test a supported accelerator.
2. Regenerate `uv.lock` and confirm no `nvidia-*`, CUDA toolkit, or Triton
   packages enter the Linux runtime unexpectedly.
3. Run the complete Python suite.
4. Build the backend and record its byte size.
5. Confirm `torch.version.cuda is None` and run a real GLiNER model load.
6. Deploy and verify engine, case-intelligence, userbot, control-bot, and health
   readiness before accepting the build.

GPU enablement is a separate deployment change requiring compatible hardware,
wheel/index selection, resource limits, performance comparison, and UAT risk
review. It must not occur implicitly through dependency resolution.

## Transitive dependency hygiene (23 August 2026)

The lockfile previously retained two yanked transitive releases even though no
direct or transitive constraint required those exact versions. A targeted lock
refresh replaced them without widening HIVE's declared dependency ranges:

| Package | Replaced | Locked and deployed | Reason |
| --- | ---: | ---: | --- |
| `grpcio` | `1.82.0` | `1.83.0` | The replaced release had incorrect protobuf dependency metadata. |
| `charset-normalizer` | `3.4.8` | `3.5.1` | The replaced release had a decoding regression. |

Validation covered more than package import. `uv lock --check` completed with
no yanked-release warning; the full Python suite passed with 352 tests, 1 skip,
and the existing Starlette deprecation warning; and the rebuilt CPU backend
contained `grpcio 1.83.0`, `charset-normalizer 3.5.1`, and
`torch 2.12.1+cpu`. After deployment, HIVE reported every runtime component
ready and `hive.verify_services` completed real PostgreSQL and Qdrant
write/read/search/cleanup round trips.

For future maintenance, prefer a targeted refresh such as
`uv lock --upgrade-package <package>` over an indiscriminate full dependency
upgrade. Then repeat the lock check, regression suite, image build, deployed
version inspection, health check, and live datastore verifier before accepting
the new lockfile.
