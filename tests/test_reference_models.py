from __future__ import annotations

import unittest

try:
    import torch
except ImportError:
    torch = None


@unittest.skipUnless(torch is not None, "optional PyTorch dependency is not installed")
class ReferenceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        assert torch is not None
        torch.manual_seed(7)

    def test_patchtst_shape_gradients_and_channel_isolation(self) -> None:
        from gridcast_public.reference_models.patchtst import PatchTSTConfig, PatchTSTReference

        model = PatchTSTReference(
            PatchTSTConfig(32, 6, 2, patch_length=8, stride=4, width=16, heads=4, depth=1)
        )
        history = torch.randn(3, 32, 2, requires_grad=True)
        output = model(history)
        self.assertEqual(tuple(output.shape), (3, 6, 2))
        self.assertTrue(torch.isfinite(output).all().item())
        output.square().mean().backward()
        self.assertIsNotNone(model.patch_projection.weight.grad)

        model.eval()
        changed = history.detach().clone()
        changed[:, :, 1] += 10
        with torch.no_grad():
            original = model(history.detach())
            modified = model(changed)
        self.assertTrue(torch.allclose(original[:, :, 0], modified[:, :, 0], atol=1e-5))

    def test_ftmixer_shape_gradients_and_channel_isolation(self) -> None:
        from gridcast_public.reference_models.ftmixer import FTMixerConfig, FTMixerReference

        model = FTMixerReference(FTMixerConfig(30, 7, 2, periods=(6, 10)))
        history = torch.randn(2, 30, 2, requires_grad=True)
        output = model(history)
        self.assertEqual(tuple(output.shape), (2, 7, 2))
        self.assertTrue(torch.isfinite(output).all().item())
        output.square().mean().backward()
        self.assertIsNotNone(model.branch_logits.grad)

        changed = history.detach().clone()
        changed[:, :, 1] -= 5
        with torch.no_grad():
            original = model(history.detach())
            modified = model(changed)
        self.assertTrue(torch.allclose(original[:, :, 0], modified[:, :, 0], atol=1e-5))

    def test_invalid_configuration_is_rejected(self) -> None:
        from gridcast_public.reference_models.ftmixer import FTMixerConfig
        from gridcast_public.reference_models.patchtst import PatchTSTConfig

        with self.assertRaises(ValueError):
            PatchTSTConfig(12, 3, 1, patch_length=16)
        with self.assertRaises(ValueError):
            FTMixerConfig(12, 3, 1, periods=(24,))


if __name__ == "__main__":
    unittest.main()
