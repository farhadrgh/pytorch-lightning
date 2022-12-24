# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import importlib
import logging
import os
from re import escape
from unittest import mock
from unittest.mock import Mock

import pytest
import torch
from tests_lite.helpers.runif import RunIf

import lightning_lite
from lightning_lite.accelerators.cuda import (
    _check_cuda_matmul_precision,
    CUDAAccelerator,
    find_usable_cuda_devices,
    is_cuda_available,
    num_cuda_devices,
)


@mock.patch("lightning_lite.accelerators.cuda.num_cuda_devices", return_value=2)
def test_auto_device_count(_):
    assert CUDAAccelerator.auto_device_count() == 2


@RunIf(min_cuda_gpus=1)
def test_gpu_availability():
    assert CUDAAccelerator.is_available()


def test_init_device_with_wrong_device_type():
    with pytest.raises(ValueError, match="Device should be CUDA"):
        CUDAAccelerator().setup_device(torch.device("cpu"))


@pytest.mark.parametrize(
    "devices,expected",
    [
        ([], []),
        ([1], [torch.device("cuda", 1)]),
        ([3, 1], [torch.device("cuda", 3), torch.device("cuda", 1)]),
    ],
)
def test_get_parallel_devices(devices, expected):
    assert CUDAAccelerator.get_parallel_devices(devices) == expected


@mock.patch("torch.cuda.set_device")
@mock.patch("torch.cuda.get_device_capability", return_value=(7, 0))
def test_set_cuda_device(_, set_device_mock):
    device = torch.device("cuda", 1)
    CUDAAccelerator().setup_device(device)
    set_device_mock.assert_called_once_with(device)


@mock.patch("lightning_lite.accelerators.cuda._device_count_nvml", return_value=-1)
@mock.patch("torch.cuda.device_count", return_value=100)
def test_num_cuda_devices_without_nvml(*_):
    """Test that if NVML can't be loaded, our helper functions fall back to the default implementation for
    determining CUDA availability."""
    num_cuda_devices.cache_clear()
    assert is_cuda_available()
    assert num_cuda_devices() == 100
    num_cuda_devices.cache_clear()


@mock.patch.dict(os.environ, {}, clear=True)
def test_force_nvml_based_cuda_check():
    """Test that we force PyTorch to use the NVML-based CUDA checks."""
    importlib.reload(lightning_lite)  # reevaluate top-level code, without becoming a different object

    assert os.environ["PYTORCH_NVML_BASED_CUDA_CHECK"] == "1"


@RunIf(min_torch="1.12")
@mock.patch("torch.cuda.get_device_capability", return_value=(10, 1))
@mock.patch("torch.cuda.get_device_name", return_value="Z100")
def test_tf32_message(_, __, caplog):
    device = Mock()
    expected = "Z100') that has Tensor Cores"
    assert torch.get_float32_matmul_precision() == "highest"  # default in torch
    with caplog.at_level(logging.INFO):
        _check_cuda_matmul_precision(device)
    assert expected in caplog.text

    caplog.clear()
    torch.backends.cuda.matmul.allow_tf32 = True  # changing this changes the string
    assert torch.get_float32_matmul_precision() == "high"
    with caplog.at_level(logging.INFO):
        _check_cuda_matmul_precision(device)
    assert not caplog.text

    caplog.clear()
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.set_float32_matmul_precision("medium")  # also the other way around
    assert torch.backends.cuda.matmul.allow_tf32
    with caplog.at_level(logging.INFO):
        _check_cuda_matmul_precision(device)
    assert not caplog.text

    torch.set_float32_matmul_precision("highest")  # can be reverted
    with caplog.at_level(logging.INFO):
        _check_cuda_matmul_precision(device)
    assert expected in caplog.text


def test_find_usable_cuda_devices_error_handling():
    """Test error handling for edge cases when using `find_usable_cuda_devices`."""

    # Asking for GPUs if no GPUs visible
    with mock.patch("lightning_lite.accelerators.cuda.num_cuda_devices", return_value=0), pytest.raises(
        ValueError, match="You requested to find 2 devices but there are no visible CUDA"
    ):
        find_usable_cuda_devices(2)

    # Asking for more GPUs than are visible
    with mock.patch("lightning_lite.accelerators.cuda.num_cuda_devices", return_value=1), pytest.raises(
        ValueError, match="this machine only has 1 GPUs"
    ):
        find_usable_cuda_devices(2)

    # All GPUs are unusable
    tensor_mock = Mock(side_effect=RuntimeError)  # simulate device placement fails
    with mock.patch("lightning_lite.accelerators.cuda.num_cuda_devices", return_value=2), mock.patch(
        "lightning_lite.accelerators.cuda.torch.tensor", tensor_mock
    ), pytest.raises(RuntimeError, match=escape("The devices [0, 1] are occupied by other processes")):
        find_usable_cuda_devices(2)