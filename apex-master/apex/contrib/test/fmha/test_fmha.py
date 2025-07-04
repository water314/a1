###############################################################################
# Copyright (c) 2011-2021, NVIDIA CORPORATION.  All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the NVIDIA CORPORATION nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL NVIDIA CORPORATION BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
###############################################################################

import math
import unittest

import torch
import numpy as np

SKIP_TEST = None
try:
    import fmhalib as mha
except ImportError as e:
    SKIP_TEST = e


def _get_device_properties(device = torch.device("cuda")):
    # type: (str or torch.device) -> Tuple[int, int]
    properties = torch.cuda.get_device_properties(device)
    return properties.major, properties.minor


def py_mha(qkv, amask, b, s, h, d):
    qkv = qkv.view(b, s, h, 3, d)
    q = qkv[:, :, :, 0, :].permute(0,2,1,3)
    k = qkv[:, :, :, 1, :].permute(0,2,1,3)
    v = qkv[:, :, :, 2, :].permute(0,2,1,3)
    p = torch.matmul(q.float(), k.permute(0,1,3,2).float())
    p_masked = p / math.sqrt(d) + (1.0 - amask) * -10000.0
    s = torch.softmax(p_masked, -1).to(qkv.dtype)
    ctx = torch.matmul(s, v)
    ctx = ctx.permute(0,2,1,3).contiguous()

    ctx.retain_grad()

    return ctx


@unittest.skipIf(SKIP_TEST, f"{SKIP_TEST}")
@unittest.skipIf(_get_device_properties() not in [(8, 0), (9, 0), (10, 0), (12, 0)], "FMHA only supports sm80")
class TestFMHA(unittest.TestCase):

    def run_test(self, s: int, b: int, zero_tensors: bool):
        print(f'Test s={s} b={b}, zero_tensors={zero_tensors}')

        torch.manual_seed(1234)
        torch.cuda.manual_seed(1234)

        dtype = torch.float16
        device = torch.device('cuda')

        h = 16
        d = 64

        slens = [s] * b
        a = torch.tensor(np.array([0] + slens), dtype=torch.int32)
        amask = torch.ones(b,h,s,s, dtype=dtype, device=device)
        seqlens = torch.tensor(slens, dtype=torch.int32, device=device)
        cu_seqlens = torch.cumsum(a, 0).to(dtype=torch.int32, device=device)
        total = cu_seqlens[-1].item()

        qkv = torch.randn((b,s,h,3,d), device=device, dtype=dtype)

        qkv_vs = qkv.permute(0,1,3,2,4).contiguous().view(b*s, 3, h,d)

        qkv.requires_grad = True

        if b < 4:
            ctx, S_ = mha.fwd(qkv_vs, cu_seqlens, 0.0, s, True, True, zero_tensors, None)
        else:
            ctx, S_ = mha.fwd(qkv_vs, cu_seqlens, 0.0, s, True, False, zero_tensors, None)
        ctx = ctx.view(b,s,h,d)

        ctx_ref = py_mha(qkv, amask, b,s,h,d)
        torch.testing.assert_close(ctx_ref.float(), ctx.float(), atol=1e-3, rtol=1e-5)

        labels = torch.randn_like(ctx_ref)
        diff = ctx_ref - labels
        l = (diff * diff).sum() / b
        l.backward()

        dw = ctx_ref.grad.permute(0,2,1,3)

        dw2 = dw.permute(0,2,1,3).clone().detach().contiguous()

        if b < 4:
            dqkv2, _, _ = mha.bwd_nl(dw2, qkv_vs, S_, cu_seqlens, 0.0, s, zero_tensors)
        else:
            dqkv2, _ = mha.bwd(dw2, qkv_vs, S_, cu_seqlens, 0.0, s, zero_tensors)

        dqkv2 = dqkv2.permute(0,2,1,3).view(b,s, h,3,d)

        torch.testing.assert_close(qkv.grad.float(), dqkv2.float(), atol=1e-3, rtol=1e-5)

    def test_128(self):
        self.run_test(128, 32, False)
        self.run_test(128, 32, True)
        self.run_test(128, 56, False)
        self.run_test(128, 56, True)

    def test_256(self):
        self.run_test(256, 32, False)
        self.run_test(256, 32, True)
        self.run_test(256, 56, False)
        self.run_test(256, 56, True)

    def test_384(self):
        self.run_test(384, 32, False)
        self.run_test(384, 32, True)
        self.run_test(384, 56, False)
        self.run_test(384, 56, True)

    def test_512(self):
        self.run_test(512, 32, False)
        self.run_test(512, 32, True)
        self.run_test(512, 56, False)
        self.run_test(512, 56, True)
        self.run_test(512, 2, False)
        self.run_test(512, 2, True)
        self.run_test(512, 3, False)
        self.run_test(512, 3, True)


if __name__ == '__main__':
    unittest.main()
