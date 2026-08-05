- AMDGPU-specific: Paths with `/Target/AMDGPU/` or `/amdgpu/`, or generic
  files where changes only affect AMDGPU (e.g., guarded by AMDGPU checks or
  AMDGPU intrinsics). Target-specific subdirectories within non-exempt top-level
  directories (e.g., `libc/.../amdgpu/`) are fully exempt.
- Flang-specific: Paths with `flang/`, or generic files where changes only
  affect Flang.
- CUDA-specific: Paths related to CUDA offload or CUDA support (e.g., CUDA
  runtime, headers, or CUDA-specific codegen). Target-specific subdirectories
  within non-exempt top-level directories (e.g., `libc/.../cuda/`) are fully
  exempt.
- LoongArch-specific: Paths with `/Target/LoongArch/` or `/LoongArch/`, or
  generic files where changes only affect LoongArch.
- libclc-specific: Paths with `libclc/`, or generic files where changes only
  affect libclc.
- COFF/DirectX-specific: Paths with `/Target/DirectX/`, `/DirectX/`, or
  COFF/DirectX-specific files.
- MLIR-specific: Paths with `mlir/`, or generic files where changes only
  affect MLIR.
