# ─────────────────────────────────────────────────
# Versa AGi — Intel ARC SYCL image for sd-cli
#
# Builds stable-diffusion.cpp with SYCL. Same oneAPI / compute-runtime
# pins as intel-sycl.Dockerfile (llama-server). Not llama-server.
#
# Usage:
#   docker build -t versa-agi-sdcpp:<tag> \
#     --build-arg="GGML_SYCL_F16=ON" \
#     -f intel-sdcpp.Dockerfile <stable-diffusion.cpp-source>
#
# Pinned source tag: master-820-de298c2 (2026-08-12)
# ─────────────────────────────────────────────────

ARG ONEAPI_VERSION=2025.3.3-0-devel-ubuntu24.04

FROM intel/deep-learning-essentials:$ONEAPI_VERSION AS build

ARG GGML_SYCL_F16=OFF
RUN apt-get update && \
    apt-get install -y git libssl-dev

WORKDIR /app
COPY . .

RUN if [ "${GGML_SYCL_F16}" = "ON" ]; then \
        export OPT_SYCL_F16="-DGGML_SYCL_F16=ON"; \
    fi && \
    cmake -B build -DSD_SYCL=ON -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx \
      -DCMAKE_BUILD_TYPE=Release ${OPT_SYCL_F16} && \
    cmake --build build --config Release -j"$(nproc)"

RUN mkdir -p /app/lib /app/bin && \
    find build -name "*.so*" -exec cp -P {} /app/lib \; && \
    if [ -f build/bin/sd-cli ]; then cp build/bin/sd-cli /app/bin/sd-cli; \
    elif [ -f bin/sd-cli ]; then cp bin/sd-cli /app/bin/sd-cli; \
    elif [ -f build/bin/sd ]; then cp build/bin/sd /app/bin/sd-cli; \
    else find build \( -name sd-cli -o -name sd \) -type f -exec cp {} /app/bin/sd-cli \; ; fi

FROM intel/deep-learning-essentials:$ONEAPI_VERSION AS runtime

ARG IGC_VERSION=v2.30.1
ARG IGC_VERSION_FULL=2_2.30.1+20950
ARG COMPUTE_RUNTIME_VERSION=26.09.37435.1
ARG COMPUTE_RUNTIME_VERSION_FULL=26.09.37435.1-0
ARG IGDGMM_VERSION=22.9.0
RUN mkdir /tmp/neo/ && cd /tmp/neo/ \
  && wget https://github.com/intel/intel-graphics-compiler/releases/download/$IGC_VERSION/intel-igc-core-${IGC_VERSION_FULL}_amd64.deb \
  && wget https://github.com/intel/intel-graphics-compiler/releases/download/$IGC_VERSION/intel-igc-opencl-${IGC_VERSION_FULL}_amd64.deb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/intel-ocloc-dbgsym_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.ddeb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/intel-ocloc_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.deb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/intel-opencl-icd-dbgsym_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.ddeb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/intel-opencl-icd_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.deb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/libigdgmm12_${IGDGMM_VERSION}_amd64.deb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/libze-intel-gpu1-dbgsym_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.ddeb \
  && wget https://github.com/intel/compute-runtime/releases/download/$COMPUTE_RUNTIME_VERSION/libze-intel-gpu1_${COMPUTE_RUNTIME_VERSION_FULL}_amd64.deb \
  && dpkg --install *.deb \
  && apt-get update && apt-get install -y libgomp1 \
  && apt autoremove -y && apt clean -y \
  && rm -rf /tmp/* /var/tmp/* \
  && find /var/cache/apt/archives /var/lib/apt/lists -not -name lock -type f -delete

COPY --from=build /app/lib/ /app/
COPY --from=build /app/bin/sd-cli /app/sd-cli

ENV LD_LIBRARY_PATH="/app:/opt/intel/oneapi/compiler/latest/lib:/opt/intel/oneapi/compiler/latest/linux/compiler/lib/intel64_lin:/opt/intel/oneapi/compiler/latest/linux/lib:/opt/intel/oneapi/umf/latest/lib:/opt/intel/oneapi/tcm/latest/lib:/opt/intel/oneapi/dnnl/latest/lib:${LD_LIBRARY_PATH}"

WORKDIR /app
ENTRYPOINT ["/app/sd-cli"]
