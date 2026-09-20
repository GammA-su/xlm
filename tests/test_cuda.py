"""CUDA acceleration smoke tests (separate from CPU suite)."""

import pytest


@pytest.mark.cuda
def test_cuda_tensor_forward_backward() -> None:
    """Verify genuine CUDA tensor allocation, forward/backward execution, and synchronization."""
    try:
        import torch
    except ImportError:
        pytest.skip("PyTorch not installed")

    if not torch.cuda.is_available():
        pytest.skip("CUDA device or CUDA torch build unavailable")

    device = torch.device("cuda")
    x = torch.randn(4, 8, device=device, requires_grad=True)
    w = torch.randn(8, 2, device=device, requires_grad=True)

    y = torch.matmul(x, w)
    loss = y.sum()
    loss.backward()  # type: ignore[no-untyped-call]

    torch.cuda.synchronize()

    assert x.grad is not None
    assert w.grad is not None
    assert torch.all(torch.isfinite(x.grad))
    assert torch.all(torch.isfinite(w.grad))
    assert float(loss.item()) != 0.0
