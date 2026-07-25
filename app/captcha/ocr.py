"""基于内置 ONNX 模型的本地图片验证码识别。"""

from __future__ import annotations

import json
import threading
from io import BytesIO
from pathlib import Path


class LocalCaptchaOcr:
    """延迟加载 ONNX 模型，并以受限尺寸执行验证码识别。"""

    _TARGET_HEIGHT = 64
    _MAX_IMAGE_BYTES = 2 * 1024 * 1024
    _MAX_SOURCE_EDGE = 4096
    _MAX_TARGET_WIDTH = 2048

    def __init__(self, resource_dir: Path | None = None):
        self._resource_dir = resource_dir or Path(__file__).resolve().parent / "resources"
        self._init_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._session = None
        self._charsets: list[str] | None = None

    def recognize(self, image_bytes: bytes) -> str:
        """识别一张验证码图片并返回去重后的字符序列。"""
        if not image_bytes:
            raise ValueError("验证码图片为空")
        if len(image_bytes) > self._MAX_IMAGE_BYTES:
            raise ValueError("验证码图片大小超限")

        self._ensure_initialized()
        tensor = self._prepare_image(image_bytes)
        with self._inference_lock:
            outputs = self._session.run(None, {"input1": tensor})
        if not outputs:
            raise RuntimeError("本地OCR输出为空")
        result = self._decode(outputs[0])
        if not result:
            raise RuntimeError("本地OCR未识别出验证码")
        return result

    def _ensure_initialized(self) -> None:
        if self._session is not None and self._charsets is not None:
            return
        with self._init_lock:
            if self._session is not None and self._charsets is not None:
                return
            try:
                import onnxruntime as ort

                model_path = self._resource_dir / "common.onnx"
                charset_path = self._resource_dir / "charsets.json"
                with charset_path.open("r", encoding="utf-8") as charset_file:
                    charsets = json.load(charset_file)
                if not isinstance(charsets, list):
                    raise ValueError("字符集格式无效")
                options = ort.SessionOptions()
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                options.intra_op_num_threads = 1
                self._session = ort.InferenceSession(
                    str(model_path),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._charsets = [str(item) for item in charsets]
            except Exception as error:
                raise RuntimeError(f"本地OCR模型初始化失败: {error}") from error

    def _prepare_image(self, image_bytes: bytes):
        try:
            import numpy as np
            from PIL import Image, ImageOps

            with Image.open(BytesIO(image_bytes)) as source:
                source = ImageOps.exif_transpose(source)
                if source.width <= 0 or source.height <= 0:
                    raise ValueError("验证码图片尺寸无效")
                if source.width > self._MAX_SOURCE_EDGE or source.height > self._MAX_SOURCE_EDGE:
                    raise ValueError("验证码图片尺寸超限")
                rgba = np.asarray(source.convert("RGBA"), dtype=np.float32)
                alpha = rgba[:, :, 3] / 255.0
                red = rgba[:, :, 0] * alpha + 255.0 * (1.0 - alpha)
                green = rgba[:, :, 1] * alpha + 255.0 * (1.0 - alpha)
                blue = rgba[:, :, 2] * alpha + 255.0 * (1.0 - alpha)
                gray = np.rint(0.2126 * red + 0.7152 * green + 0.0722 * blue)
                target_width = max(
                    1,
                    min(
                        self._MAX_TARGET_WIDTH,
                        int(source.width * (self._TARGET_HEIGHT / source.height)),
                    ),
                )
                x = np.arange(target_width, dtype=np.float32) * (source.width / target_width)
                y = np.arange(self._TARGET_HEIGHT, dtype=np.float32) * (
                    source.height / self._TARGET_HEIGHT
                )
                x1 = np.floor(x).astype(np.int32)
                y1 = np.floor(y).astype(np.int32)
                x2 = np.minimum(x1 + 1, source.width - 1)
                y2 = np.minimum(y1 + 1, source.height - 1)
                fx = x - x1
                fy = y - y1
                values = (
                    gray[y1[:, None], x1[None, :]]
                    * (1.0 - fx[None, :])
                    * (1.0 - fy[:, None])
                    + gray[y1[:, None], x2[None, :]]
                    * fx[None, :]
                    * (1.0 - fy[:, None])
                    + gray[y2[:, None], x1[None, :]]
                    * (1.0 - fx[None, :])
                    * fy[:, None]
                    + gray[y2[:, None], x2[None, :]]
                    * fx[None, :]
                    * fy[:, None]
                )
                values = np.rint(values).astype(np.float32) / 255.0
                return values.reshape(1, 1, self._TARGET_HEIGHT, target_width)
        except ValueError:
            raise
        except Exception as error:
            raise RuntimeError(f"验证码图片预处理失败: {error}") from error

    def _decode(self, output: object) -> str:
        import numpy as np

        if self._charsets is None:
            raise RuntimeError("本地OCR字符集未初始化")
        result: list[str] = []
        previous = -1
        for raw in np.asarray(output).reshape(-1):
            index = int(round(float(raw)))
            if index == previous:
                continue
            previous = index
            if 0 < index < len(self._charsets):
                character = self._charsets[index]
                if character:
                    result.append(character)
        return "".join(result).strip()
