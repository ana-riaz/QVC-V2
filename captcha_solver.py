import base64
import asyncio
import io
import httpx
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def _ascii_alnum(s: str) -> str:
    """Keep only ASCII letters and digits — rejects CJK/unicode garbage."""
    return ''.join(c for c in s if c.isascii() and c.isalnum())


def _preprocess_captcha(image_bytes: bytes) -> bytes:
    """Grayscale + contrast boost — improves ddddocr accuracy on noisy CAPTCHAs."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        img = Image.open(io.BytesIO(image_bytes)).convert('L')
        img = ImageEnhance.Contrast(img).enhance(2.5)
        img = img.filter(ImageFilter.SHARPEN)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception:
        return image_bytes  # fall back to original if Pillow unavailable


class CaptchaSolver:
    def __init__(self, capsolver_api_key: str, twocaptcha_api_key: str = ""):
        self.capsolver_api_key = capsolver_api_key
        self.twocaptcha_api_key = twocaptcha_api_key
        self.ocr = None
        self.ocr_beta = None
        self._client = None
        self._init_local_ocr()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create reusable HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def close(self):
        """Close HTTP client - call on shutdown"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _init_local_ocr(self):
        """Initialize ddddocr (default + beta model) for local solving."""
        try:
            import ddddocr
            self.ocr = ddddocr.DdddOcr(show_ad=False)
            try:
                self.ocr_beta = ddddocr.DdddOcr(show_ad=False, beta=True)
            except Exception:
                self.ocr_beta = None
            logger.info("Local OCR (ddddocr) initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize ddddocr: {e}")
            self.ocr = None

    def solve_local(self, image_bytes: bytes) -> Optional[str]:
        if not self.ocr:
            logger.warning("Local OCR skipped: ddddocr not initialized")
            return None

        preprocessed = _preprocess_captcha(image_bytes)

        # Try default model on preprocessed image, then original
        for ocr, img, label in [
            (self.ocr,      preprocessed,  "default+preprocess"),
            (self.ocr,      image_bytes,   "default+raw"),
            (self.ocr_beta, preprocessed,  "beta+preprocess"),
            (self.ocr_beta, image_bytes,   "beta+raw"),
        ]:
            if ocr is None:
                continue
            try:
                raw = ocr.classification(img)
                cleaned = _ascii_alnum(raw)
                logger.info(f"Local OCR [{label}]: raw={raw!r} cleaned={cleaned!r}")
                if len(cleaned) >= 4:
                    logger.info(f"Local OCR succeeded ({label}): {cleaned}")
                    return cleaned
            except Exception as e:
                logger.debug(f"Local OCR [{label}] error: {e}")

        logger.info("Local OCR: all attempts produced no valid result")
        return None
    
    async def solve_capsolver(self, image_base64: str) -> Optional[str]:

        payload = {
            "clientKey": self.capsolver_api_key,
            "task": {
                "type": "ImageToTextTask",
                "body": image_base64,
                "module": "common",  # General image captcha
                "score": 0.9
            }
        }
        
        try:
            # Use pooled HTTP client
            client = await self._get_client()
            
            # Create task
            response = await client.post(
                "https://api.capsolver.com/createTask",
                json=payload
            )
            data = response.json()
            
            if data.get("errorId", 0) != 0:
                logger.error(f"CapSolver error: {data.get('errorDescription')}")
                return None

            # ImageToTextTask returns solution immediately in createTask response
            solution = data.get("solution", {})
            if solution and solution.get("text"):
                text = solution["text"]
                logger.info(f"CapSolver result (instant): {text}")
                return text

            # Fallback: poll if no instant result
            task_id = data.get("taskId")
            if not task_id:
                logger.error("CapSolver: no solution and no taskId")
                return None

            for _ in range(30):
                await asyncio.sleep(2)
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={
                        "clientKey": self.capsolver_api_key,
                        "taskId": task_id
                    }
                )
                result_data = result_response.json()

                if result_data.get("status") == "ready":
                    text = result_data.get("solution", {}).get("text")
                    logger.info(f"CapSolver result: {text}")
                    return text

                if result_data.get("errorId", 0) != 0:
                    logger.error(f"CapSolver task error: {result_data}")
                    return None

            logger.error("CapSolver timeout")
            return None
                
        except Exception as e:
            logger.error(f"CapSolver API failed: {e}")
            return None
    
    async def solve_2captcha(self, image_base64: str) -> Optional[str]:
        if not self.twocaptcha_api_key:
            return None
        payload = {
            "clientKey": self.twocaptcha_api_key,
            "task": {
                "type": "ImageToTextTask",
                "body": image_base64,
                "phrase": False,
                "case": True,
                "numeric": 0,
                "math": False,
                "minLength": 1,
                "maxLength": 5,
                "comment": "enter the text you see on the image"
            },
            "languagePool": "en"
        }
        try:
            client = await self._get_client()
            response = await client.post("https://api.2captcha.com/createTask", json=payload)
            data = response.json()

            if data.get("errorId", 0) != 0:
                logger.error(f"2Captcha error: {data.get('errorDescription')}")
                return None

            task_id = data.get("taskId")
            if not task_id:
                logger.error("2Captcha: no taskId in response")
                return None

            for _ in range(30):
                await asyncio.sleep(2)
                result_resp = await client.post(
                    "https://api.2captcha.com/getTaskResult",
                    json={"clientKey": self.twocaptcha_api_key, "taskId": task_id}
                )
                result = result_resp.json()
                if result.get("status") == "ready":
                    text = result.get("solution", {}).get("text")
                    logger.info(f"2Captcha result: {text}")
                    return text
                if result.get("errorId", 0) != 0:
                    logger.error(f"2Captcha task error: {result}")
                    return None

            logger.error("2Captcha timeout")
            return None
        except Exception as e:
            logger.error(f"2Captcha API failed: {e}")
            return None

    async def solve(self, image_data: str | bytes, max_retries: int = 3) -> Optional[str]:

        # Normalize input
        if isinstance(image_data, str):
            # Remove data URL prefix if present
            if "base64," in image_data:
                image_data = image_data.split("base64,")[1]
            image_bytes = base64.b64decode(image_data)
            image_base64 = image_data
        else:
            image_bytes = image_data
            image_base64 = base64.b64encode(image_data).decode()
        
        for attempt in range(max_retries):
            logger.info(f"CAPTCHA solve attempt {attempt + 1}/{max_retries}")

            # 1st priority: 2Captcha API
            if self.twocaptcha_api_key:
                result = await self.solve_2captcha(image_base64)
                if result:
                    logger.info(f"2Captcha succeeded: {result}")
                    return result
                logger.info("2Captcha failed, trying local OCR...")

            # 2nd: local OCR (fast, free)
            result = self.solve_local(image_bytes)
            if result:
                logger.info(f"Local OCR succeeded: {result}")
                return result

            # 3rd: CapSolver fallback
            logger.info("Local OCR failed, trying CapSolver...")
            result = await self.solve_capsolver(image_base64)
            if result:
                logger.info(f"CapSolver succeeded: {result}")
                return result

            logger.warning(f"Attempt {attempt + 1} failed")
        
        logger.error("All CAPTCHA solve attempts exhausted")
        return None


# Quick test
if __name__ == "__main__":
    import sys
    
    async def test():
        solver = CaptchaSolver("CAP-TEST-KEY")
        # Test with sample base64 image
        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as f:
                result = await solver.solve(f.read())
                print(f"Result: {result}")
    
    asyncio.run(test())
