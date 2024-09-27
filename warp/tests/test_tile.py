# Copyright (c) 2024 NVIDIA CORPORATION.  All rights reserved.
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

import unittest

import numpy as np

import warp as wp
from warp.tests.unittest_utils import *

TILE_M = wp.constant(8)
TILE_N = wp.constant(4)
TILE_K = wp.constant(8)

# num threads per-tile
TILE_DIM = 64


@wp.kernel
def tile_copy(A: wp.array2d(dtype=float), B: wp.array2d(dtype=float)):
    # tile index
    i, j = wp.tid()

    a = wp.tile_load(A, i, j, m=TILE_M, n=TILE_N)
    wp.tile_store(B, i, j, a)


def test_tile_copy(test, device):
    rng = np.random.default_rng(42)

    M = TILE_M * 7
    N = TILE_N * 5

    A = rng.random((M, N), dtype=np.float32)
    B = rng.random((M, N), dtype=np.float32)

    A_wp = wp.array(A, requires_grad=True, device=device)
    B_wp = wp.array(B, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_copy,
            dim=[int(M / TILE_M), int(N / TILE_N)],
            inputs=[A_wp, B_wp],
            block_dim=TILE_DIM,
            device=device,
        )

    # verify forward pass
    assert_array_equal(B_wp, A_wp)

    # verify backward pass
    B_wp.grad = wp.ones_like(B_wp, device=device)
    tape.backward()

    assert_array_equal(B_wp.grad, A_wp.grad)


@wp.func
def unary_func(x: float):
    return wp.sin(x)


@wp.kernel
def tile_unary_map(input: wp.array2d(dtype=float), output: wp.array2d(dtype=float)):
    # tile index
    i, j = wp.tid()

    a = wp.tile_load(input, i, j, m=TILE_M, n=TILE_N)

    sa = wp.tile_map(wp.sin, a)

    wp.tile_store(output, i, j, sa)


def test_tile_unary_map(test, device):
    rng = np.random.default_rng(42)

    M = TILE_M * 7
    N = TILE_N * 5

    A = rng.random((M, N), dtype=np.float32)
    B = np.sin(A)

    A_grad = np.cos(A)

    A_wp = wp.array(A, requires_grad=True, device=device)
    B_wp = wp.zeros_like(A_wp, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_unary_map,
            dim=[int(M / TILE_M), int(N / TILE_N)],
            inputs=[A_wp, B_wp],
            block_dim=TILE_DIM,
            device=device,
        )

    # verify forward pass
    assert_np_equal(B_wp.numpy(), B, tol=1.0e-4)

    # verify backward pass
    B_wp.grad = wp.ones_like(B_wp, device=device)
    tape.backward()

    assert_np_equal(A_wp.grad.numpy(), A_grad, tol=1.0e-6)


@wp.func
def binary_func(x: float, y: float):
    return wp.sin(x) + y


@wp.kernel
def tile_binary_map(
    input_a: wp.array2d(dtype=float), input_b: wp.array2d(dtype=float), output: wp.array2d(dtype=float)
):
    # tile index
    i, j = wp.tid()

    a = wp.tile_load(input_a, i, j, m=TILE_M, n=TILE_N)
    b = wp.tile_load(input_b, i, j, m=TILE_M, n=TILE_N)

    sa = wp.tile_map(binary_func, a, b)

    wp.tile_store(output, i, j, sa)


def test_tile_binary_map(test, device):
    rng = np.random.default_rng(42)

    M = TILE_M * 7
    N = TILE_N * 5

    A = rng.random((M, N), dtype=np.float32)
    B = rng.random((M, N), dtype=np.float32)
    C = np.sin(A) + B

    A_grad = np.cos(A)
    B_grad = np.ones_like(B)

    A_wp = wp.array(A, requires_grad=True, device=device)
    B_wp = wp.array(B, requires_grad=True, device=device)
    C_wp = wp.zeros_like(A_wp, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_binary_map,
            dim=[int(M / TILE_M), int(N / TILE_N)],
            inputs=[A_wp, B_wp, C_wp],
            block_dim=TILE_DIM,
            device=device,
        )

    # verify forward pass
    assert_np_equal(C_wp.numpy(), C, tol=1.0e-6)

    # verify backward pass
    C_wp.grad = wp.ones_like(C_wp, device=device)
    tape.backward()

    assert_np_equal(A_wp.grad.numpy(), A_grad, tol=1.0e-6)
    assert_np_equal(B_wp.grad.numpy(), B_grad)


@wp.kernel
def tile_grouped_gemm(A: wp.array3d(dtype=float), B: wp.array3d(dtype=float), C: wp.array3d(dtype=float)):
    # output tile index
    i = wp.tid()

    a = wp.tile_load(A[i], 0, 0, m=TILE_M, n=TILE_K)
    b = wp.tile_load(B[i], 0, 0, m=TILE_K, n=TILE_N)

    sum = wp.tile_zeros(m=TILE_M, n=TILE_N, dtype=wp.float32)

    wp.tile_matmul(a, b, sum)

    wp.tile_store(C[i], 0, 0, sum)


def test_tile_grouped_gemm(test, device):
    batch_count = 56

    M = TILE_M
    N = TILE_N
    K = TILE_K

    rng = np.random.default_rng(42)
    A = rng.random((batch_count, M, K), dtype=np.float32)
    B = rng.random((batch_count, K, N), dtype=np.float32)
    C = A @ B

    A_wp = wp.array(A, requires_grad=True, device=device)
    B_wp = wp.array(B, requires_grad=True, device=device)
    C_wp = wp.zeros((batch_count, TILE_M, TILE_N), requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(tile_grouped_gemm, 
                  dim=[batch_count],
                  inputs=[A_wp, B_wp, C_wp], 
                  block_dim=TILE_DIM, 
                  device=device)

    # TODO: 32 mismatched elements
    assert_np_equal(C_wp.numpy(), C)


@wp.kernel
def tile_gemm(A: wp.array2d(dtype=float), B: wp.array2d(dtype=float), C: wp.array2d(dtype=float)):
    # output tile index
    i, j = wp.tid()

    sum = wp.tile_zeros(m=TILE_M, n=TILE_N, dtype=wp.float32)

    M = A.shape[0]
    N = B.shape[1]
    K = A.shape[1]

    count = int(K / TILE_K)

    for k in range(0, count):
        a = wp.tile_load(A, i, k, m=TILE_M, n=TILE_K)
        b = wp.tile_load(B, k, j, m=TILE_K, n=TILE_N)

        # sum += a*b
        wp.tile_matmul(a, b, sum)

    wp.tile_store(C, i, j, sum)


def test_tile_gemm(test, device):
    M = TILE_M * 7
    K = TILE_K * 6
    N = TILE_N * 5

    rng = np.random.default_rng(42)
    A = rng.random((M, K), dtype=np.float32)
    B = rng.random((K, N), dtype=np.float32)
    C = np.zeros((M, N), dtype=np.float32)

    A_wp = wp.array(A, requires_grad=True, device=device)
    B_wp = wp.array(B, requires_grad=True, device=device)
    C_wp = wp.array(C, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_gemm,
            dim=(int(M / TILE_M), int(N / TILE_N)),
            inputs=[A_wp, B_wp, C_wp],
            block_dim=TILE_DIM,
            device=device,
        )

    assert_np_equal(C_wp.numpy(), A @ B, tol=1.0e-5)

    adj_C = np.ones_like(C)

    tape.backward(grads={C_wp: wp.array(adj_C, device=device)})

    assert_np_equal(A_wp.grad.numpy(), adj_C @ B.T, tol=1.0e-5)
    assert_np_equal(B_wp.grad.numpy(), A.T @ adj_C, 1.0e-5)


@wp.kernel
def tile_operators(input: wp.array3d(dtype=float), output: wp.array3d(dtype=float)):
    # output tile index
    i = wp.tid()

    a = wp.tile_load(input[i], 0, 0, m=TILE_M, n=TILE_N)

    # neg
    b = -a

    # right scalar multiply
    c = b * 0.5

    # left scalar multiply
    d = 0.5 * c

    # add tiles
    e = a + d

    wp.tile_store(output[i], 0, 0, e)


def test_tile_operators(test, device):
    batch_count = 56

    M = TILE_M
    N = TILE_N

    rng = np.random.default_rng(42)
    input = rng.random((batch_count, M, N), dtype=np.float32)
    output = input * 0.75

    input_wp = wp.array(input, requires_grad=True, device=device)
    output_wp = wp.zeros_like(input_wp, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_operators, 
            dim=[batch_count], 
            inputs=[input_wp, output_wp], 
            block_dim=TILE_DIM, 
            device=device)

    assert_np_equal(output_wp.numpy(), output)

    output_wp.grad.fill_(1.0)

    tape.backward()

    assert_np_equal(input_wp.grad.numpy(), np.ones_like(input) * 0.75)


@wp.kernel
def tile_sum_kernel(input: wp.array3d(dtype=float), output: wp.array(dtype=float)):
    # output tile index
    i = wp.tid()

    a = wp.tile_load(input[i], 0, 0, m=TILE_M, n=TILE_N)
    s = wp.tile_sum(a) * 0.5

    wp.tile_store(output, i, 0, s)


def test_tile_sum(test, device):
    batch_count = 56

    M = TILE_M
    N = TILE_N

    rng = np.random.default_rng(42)
    input = rng.random((batch_count, M, N), dtype=np.float32)

    input_wp = wp.array(input, requires_grad=True, device=device)
    output_wp = wp.zeros(batch_count, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_sum_kernel,
            dim=[batch_count],
            inputs=[input_wp, output_wp],
            block_dim=TILE_DIM,
            device=device,
        )

    sum_wp = output_wp.numpy()

    for i in range(batch_count):
        sum_np = np.sum(input[i]) * 0.5
        test.assertAlmostEqual(sum_wp[i], sum_np, places=5)

    output_wp.grad.fill_(1.0)

    tape.backward()

    assert_np_equal(input_wp.grad.numpy(), np.ones_like(input) * 0.5)


@wp.kernel
def tile_extract_kernel(input: wp.array2d(dtype=float), output: wp.array2d(dtype=float)):
    # output tile index
    i = wp.tid()

    t = wp.tile_load(input, 0, 0, m=TILE_M, n=TILE_N)

    # perform a scalar copy, extracting each
    # tile element individually
    for i in range(TILE_M):
        for j in range(TILE_N):
            output[i, j] = t[i, j]


def test_tile_extract(test, device):
    M = TILE_M
    N = TILE_N

    rng = np.random.default_rng(42)
    input = rng.random((M, N), dtype=np.float32)

    input_wp = wp.array(input, requires_grad=True, device=device)
    output_wp = wp.zeros_like(input_wp, requires_grad=True, device=device)

    with wp.Tape() as tape:
        wp.launch_tiled(
            tile_extract_kernel, 
            dim=[1], 
            inputs=[input_wp, output_wp], 
            block_dim=TILE_DIM, 
            device=device)

    assert_array_equal(output_wp, input_wp)

    output_wp.grad.fill_(1.0)

    tape.backward()

    assert_np_equal(input_wp.grad.numpy(), np.ones_like(input))


# #-----------------------------------------
# # center of mass computation

# start = offset[i]
# end = offset[i+1]

# com = wp.tile_zeros(dtype=wp.vec3, M=1)

# # load chunks of indices
# for i in range(start, end, N):

#     count = wp.min(N, end-i)

#     idx = wp.tile_load(indices, i, N, max_col=count)
#     p = wp.tile_load(points, idx, max_col=count)

#     com += wp.tile_sum(p)


# wp.tile_store(out[i], com)


# #-------------------------------------------
# # compute deformation gradient

# i =
# j =
# k =
# l =

# f = wp.tile(F)  # generate a block size tile of feature vectors

# # layer 1
# w1 = wp.tile_load(weights)
# b1 = wp.tile_load(bias)

# z = wp.tile_matmul(w1, f) + b1
# z = wp.tile_map(relu, z)

# # layer 2
# w2 = wp.tile_load(weights)
# b2 = wp.tile_load(bias)

# z = wp.tile_matmul(w2, z) + b2
# z = wp.tile_map(relu, z)

# o = wp.untile(f)


# #----------------------------------
# # MLP with helper function for linear layers
# # where shape is only partially known
# # at compile time, and the other dims
# # are inferred from the input vector

# f = wp.tile(F)

# z = wp.tile_linear(weights1, bias1, f, hidden=16)
# z = wp.tile_map(relu, z)

# z = wp.tile_linear(weights2, bias2, f, hidden=8)
# z = wp.tile_map(relu, z)

# z = wp.tile_linear(weights3, bias3, f, hidden=4)
# z = wp.tile_map(relu, z)

# o = wp.untile(z)


# #----------------------------------
# # softmax

# def softmax(z: Any):

#     e = wp.tile_map(wp.exp, z)
#     s = wp.tile_sum(e, dim=0)

#     return z/s[0]

devices = get_cuda_test_devices()


class TestTile(unittest.TestCase):
    pass


add_function_test(TestTile, "test_tile_copy", test_tile_copy, devices=devices)
add_function_test(TestTile, "test_tile_unary_map", test_tile_unary_map, devices=devices)
add_function_test(TestTile, "test_tile_binary_map", test_tile_binary_map, devices=devices)
add_function_test(TestTile, "test_tile_grouped_gemm", test_tile_grouped_gemm, devices=devices)  # FAILS
add_function_test(TestTile, "test_tile_gemm", test_tile_gemm, devices=devices)
add_function_test(TestTile, "test_tile_operators", test_tile_operators, devices=devices)
add_function_test(TestTile, "test_tile_sum", test_tile_sum, devices=devices)
add_function_test(TestTile, "test_tile_extract", test_tile_extract, devices=devices)


if __name__ == "__main__":
    wp.clear_kernel_cache()
    unittest.main(verbosity=2)
