"""OCR preprocessing and text extraction utilities.

Provides image preprocessing functions (preprocessImage,
advancedProcessing, cropToRegion, cropToTextWithBorder) and
OCR extraction helpers (ocr_number, ocr_text,
preprocess_and_ocr_number). All work with cv2/MatLike images."""

import re
from typing import Literal, Tuple

import cv2
import numpy as np
import tesserocr
from cv2.typing import MatLike
from PIL import Image


def cropToRegion(image: MatLike, roi: Tuple[int, int, int, int]) -> MatLike:
    """Crops an image to a region.

    Args:
        image (MatLike): The image to crop
        roi (Tuple[int, int, int, int]): The region to crop to in (x, y, w, h)

    Returns:
        MatLike: The cropped image
    """
    return image[int(roi[1]) : int(roi[1] + roi[3]), int(roi[0]) : int(roi[0] + roi[2])]


def cropToTextWithBorder(img: MatLike, border_size: int) -> MatLike:
    """Crops a black and white picture to the smallest size containing non white pixels.

    Args:
        img (MatLike): The image to crop
        border_size (int): The border to add around the detected pixels

    Returns:
        MatLike: The cropped image
    """
    coords = cv2.findNonZero(cv2.bitwise_not(img))
    x, y, w, h = cv2.boundingRect(coords)

    roi = img[y : y + h, x : x + w]
    bordered = cv2.copyMakeBorder(
        roi,
        top=border_size,
        bottom=border_size,
        left=border_size,
        right=border_size,
        borderType=cv2.BORDER_CONSTANT,
        value=[255],
    )

    return bordered


def preprocessImage(
    image: MatLike,
    scale_factor: int,
    threshold: int,
    border_size: int,
    invert: bool = False,
) -> MatLike:
    """Preprocesses an image to improve OCR quality.

    First a scaling factor gets applied, then the image is converted to
    grey scale. After that it is inverted if needed and then gets cropped
    to the smallest region containing black pixels with a border around them.

    Args:
        image (MatLike): The image to preprocess
        scale_factor (int): The factor the image is scaled
        threshold (int): The threshold to use for black and white conversion
        border_size (int): The border to add around the black pixels
        invert (bool): Whether to invert the image or not (Default value = False)

    Returns:
        MatLike: The preprocessed image
    """
    im_big = cv2.resize(image, (0, 0), fx=scale_factor, fy=scale_factor)
    im_gray = cv2.cvtColor(im_big, cv2.COLOR_BGR2GRAY)
    if invert:
        im_gray = cv2.bitwise_not(im_gray)
    (_, im_bw) = cv2.threshold(im_gray, threshold, 255, cv2.THRESH_BINARY)
    im_bw = cropToTextWithBorder(im_bw, border_size)
    return im_bw


def advancedProcessing(
    image: MatLike, scale_factor: int, mode: Literal["white", "dimmed white", "black"]
) -> MatLike:
    """Preprocesses an image to improve OCR quality.

    First a scaling factor gets applied, then the image is converted to
    HSV scale. After that a mask depending on the expected color gets
    applied. The final processing is to dilute that mask and invert it
    to get black text.

    Args:
        image (MatLike): The image to preprocess
        scale_factor (int): The factor the image is scaled
        mode (Literal['white', 'dimmed white', 'black']): The color of the text to detect

    Returns:
        MatLike: The preprocessed image
    """
    kernel = np.ones((2, 2), np.uint8)
    im_big = cv2.resize(image, (0, 0), fx=scale_factor, fy=scale_factor)
    hsv = cv2.cvtColor(im_big, cv2.COLOR_BGR2HSV)

    match mode:
        case "black":
            lower = np.array([0, 0, 0])  # Acclaim and ID
            upper = np.array([255, 255, 30])  # Acclaim and ID
        case "dimmed white":
            lower = np.array([0, 0, 210])  # Acclaim and ID
            upper = np.array([255, 255, 255])  # Acclaim and ID
        case "white":
            lower = np.array([0, 0, 220])  # Acclaim and ID
            upper = np.array([255, 255, 255])  # Acclaim and ID

    mask = cv2.inRange(hsv, lower, upper)
    result = cv2.dilate(mask, kernel, iterations=1)
    result = cv2.bitwise_not(result)  # Need to invert to make text black
    return result


def _finish_binary(binary: MatLike, border_size: int) -> MatLike:
    """Adds the white margin around a black-on-white image.

    Args:
        binary (MatLike): Text in black on white
        border_size (int): White margin to add

    Returns:
        MatLike: The image with margin
    """
    if border_size <= 0:
        return binary
    return cv2.copyMakeBorder(
        binary,
        top=border_size,
        bottom=border_size,
        left=border_size,
        right=border_size,
        borderType=cv2.BORDER_CONSTANT,
        value=[255],
    )


def tophatProcessing(
    image: MatLike, scale_factor: int = 3, kernel_size: int = 7, border_size: int = 10
) -> MatLike:
    """Extracts bright text from a busy or changing background.

    A white top-hat keeps only bright shapes thinner than the kernel (text) and
    removes the slowly changing background, Otsu then picks the threshold for
    every crop on its own. Works for the governor profile skins, where the
    background behind the text can be anything.

    Args:
        image (MatLike): The cropped image in BGR format
        scale_factor (int): How much to enlarge the image (Default value = 3)
        kernel_size (int): Thickness of text to keep, in unscaled pixels (Default value = 7)
        border_size (int): White margin added around the result (Default value = 10)

    Returns:
        MatLike: The preprocessed image with black text on white
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC
    )
    size = (kernel_size * scale_factor // 2) * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    _, binary = cv2.threshold(tophat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _finish_binary(cv2.bitwise_not(binary), border_size)


def relativeThresholdProcessing(
    image: MatLike, scale_factor: int = 3, fraction: float = 0.45, border_size: int = 10
) -> MatLike:
    """Keeps only the brightest pixels of the crop, relative to the crop itself.

    The threshold sits at the given fraction between the brightest and the
    median pixel, so it follows text that is dimmed or lit differently.

    Args:
        image (MatLike): The cropped image in BGR format
        scale_factor (int): How much to enlarge the image (Default value = 3)
        fraction (float): How far below the brightest pixel to cut (Default value = 0.45)
        border_size (int): White margin added around the result (Default value = 10)

    Returns:
        MatLike: The preprocessed image with black text on white
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC
    )
    brightest = float(gray.max())
    threshold = brightest - fraction * (brightest - float(np.median(gray)))
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    return _finish_binary(cv2.bitwise_not(binary), border_size)


def otsuProcessing(
    image: MatLike, scale_factor: int = 3, border_size: int = 12
) -> MatLike:
    """Black text on white via Otsu thresholding, for text on a flat background.

    Whichever color is the text, it ends up black: the minority color is taken
    as the text.

    Args:
        image (MatLike): The cropped image in BGR format
        scale_factor (int): How much to enlarge the image (Default value = 3)
        border_size (int): White margin added around the result (Default value = 12)

    Returns:
        MatLike: The preprocessed image with black text on white
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC
    )
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() < 127:  # mostly black -> text is the white part
        binary = cv2.bitwise_not(binary)
    return _finish_binary(binary, border_size)


def read_governor_id(
    api: tesserocr.PyTessBaseAPI,
    image: MatLike,
    region: Tuple[int, int, int, int],
    min_length: int = 6,
    max_length: int = 10,
) -> str:
    """Reads the governor id from a profile screenshot.

    The id is small text over the profile skin, which can be anything from a plain
    color to a busy picture. So several preprocessing methods are tried one after
    the other and the first result with a believable length wins. If none has one,
    the longest result is returned.

    Args:
        api (tesserocr.PyTessBaseAPI): The ocr api to use
        image (MatLike): The profile screenshot in BGR format
        region (Tuple[int, int, int, int]): The id region in (x, y, w, h) format
        min_length (int): Shortest believable id (Default value = 6)
        max_length (int): Longest believable id (Default value = 10)

    Returns:
        str: The detected digits
    """
    crop = cropToRegion(image, region)
    methods = (
        lambda: tophatProcessing(crop, 3, 7),
        lambda: advancedProcessing(crop, 3, "dimmed white"),
        lambda: relativeThresholdProcessing(crop, 3, 0.45),
    )

    api.SetVariable("tessedit_char_whitelist", "0123456789")
    try:
        best = ""
        for method in methods:
            digits = ocr_number(api, method(), empty_retry=False)
            if min_length <= len(digits) <= max_length:
                return digits
            if len(digits) > len(best):
                best = digits
        return best
    finally:
        api.SetVariable("tessedit_char_whitelist", "")


def ocr_number(
    api: tesserocr.PyTessBaseAPI, image: MatLike, empty_retry: bool = True
) -> str:
    """Extracts a integer from an image.

    This function extract the text from the given image, then strips it
    with regex to only contain digits. If the first try produces an
    empty text a second try with a scaled down version of the image gets
    attempted.

    Args:
        api (tesserocr.PyTessBaseAPI): The ocr api to use
        image (MatLike): The image to analyze
        empty_retry (bool): Whether to try a second time if first fails (Default value = True)

    Returns:
        str: The digits that got detected
    """
    api.SetImage(Image.fromarray(image))  # type: ignore
    score_raw = api.GetUTF8Text()
    score = re.sub("[^0-9]", "", score_raw)

    if score == "" and empty_retry:
        img_try_2 = cv2.resize(image, (0, 0), fx=0.5, fy=0.5)
        api.SetImage(Image.fromarray(img_try_2))  # type: ignore
        score_raw = api.GetUTF8Text()
        score = re.sub("[^0-9]", "", score_raw)

    return score


def ocr_text(api: tesserocr.PyTessBaseAPI, image: MatLike) -> str:
    """Extracts a string from the image.

    Args:
        api (tesserocr.PyTessBaseAPI): The ocr api to use
        image (MatLike): The image to analyze

    Returns:
        str: The detected string
    """
    api.SetImage(Image.fromarray(image))  # type: ignore
    name = api.GetUTF8Text()
    return name.rstrip("\n")


def preprocess_and_ocr_number(
    api: tesserocr.PyTessBaseAPI,
    image: MatLike,
    region: Tuple[int, int, int, int],
    invert: bool = False,
) -> str:
    """Combines the preprocessing and ocr into a single method.

    Args:
        api (tesserocr.PyTessBaseAPI): The ocr api to use
        image (MatLike): The image to analyze
        region (Tuple[int, int, int, int]): The region to analyze in (x, y, w, h) format
        invert (bool): Whether to invert the image before OCR (Default value = False)

    Returns:
        str: The detected digits
    """
    cropped_image = cropToRegion(image, region)
    cropped_bw_image = preprocessImage(cropped_image, 3, 150, 12, invert)

    return ocr_number(api, cropped_bw_image)


def get_supported_langs(path: str) -> str:
    """Gets the supported languages from the ocr engine.

    Args:
        path (str): Path to the trained data

    Returns:
        str: All supported languages
    """
    return str(tesserocr.get_languages(path))  # type: ignore
