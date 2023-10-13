#!/usr/bin/env python3

try:
    import platform
    import depthai as dai
    import time
    import inspect
    import textwrap
    import sys
    import signal
    import threading
    from depthai_sdk.managers import arg_manager
    import blobconverter
except ImportError as e:
    print(e, f"""
did you remember to source (or create) a venv with the dependencies?
e.g.
source depthai_venv/bin/activate.fish
pip3 install -r requirements.txt
""")
    raise SystemExit(1)

args = arg_manager.parseArgs()

if platform.machine() == 'aarch64':
    print("This app is temporarily disabled on AARCH64 systems due to an issue with stream preview. We are working on resolving this issue", file=sys.stderr)
    raise SystemExit(1)

"""
Goal: using the 4k full-isp color camera, automatically crop a 1080P image and feed that to the host using UVC.
- on the way, use a face tracking NN and center the crop on whichever face is detected


We need a couple different nodes:
- cam_rgb is a dai.node.ColorCamera
- mobilenet is a dai.node.MobileNetDetectionNetwork
- crop_manip is an dai.node.ImageManip
- script is a dai.node.Script

- uvc is a pipeline.createUVC() ... which is different syntax?

The layout of these pipelines is like this:
cam_rgb -> preview[300,300] -> mobilenet -> script -> crop_manip
|-> crop_manip -> uvc

"""

# root pipeline definition
pipeline = dai.Pipeline()

# source for all inputs is the 4k color camera
cam_rgb = pipeline.create(dai.node.ColorCamera)
cam_rgb.setBoardSocket(dai.CameraBoardSocket.RGB)
cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_4_K)
cam_rgb.setIspScale(1, 2)

# set the preview to squash the full frame down to 300x300 for the mobilenet input
cam_rgb.setPreviewKeepAspectRatio(False)
cam_rgb.setPreviewSize(300, 300)
cam_rgb.initialControl.setManualFocus(130)
cam_rgb.setInterleaved(False)

# Create MobileNet detection network
mobilenet = pipeline.create(dai.node.MobileNetDetectionNetwork)
mobilenet.setBlobPath(blobconverter.from_zoo(
    name="face-detection-retail-0004", shaves=5))
mobilenet.setConfidenceThreshold(0.7)
cam_rgb.preview.link(mobilenet.input)

# bogus definitions so that pylance shuts up


def ImageManipConfig():
    return 1


def Size2f():
    return 1


class node:
    def io():
        return 1


class ImgFrame:
    def Type():
        return 1


def RotatedRect():
    return 1


def Point2f():
    return 1

# This code is never used by the host, it is uploaded to a script node on the device
# Slight shennanigans are used to make this editable with all the nicities of a normal IDE, but it's uploaded as a raw string


def onboardScripting():
    ORIGINAL_SIZE = (3840, 2160)  # 4K
    SCENE_SIZE = (1920, 1080)  # 1080P
    x_arr = []
    y_arr = []
    AVG_MAX_NUM = 7
    limits = [SCENE_SIZE[0] // 2, SCENE_SIZE[1] // 2]  # xmin and ymin limits
    limits.append(ORIGINAL_SIZE[0] - limits[0])  # xmax limit
    limits.append(ORIGINAL_SIZE[1] - limits[1])  # ymax limit

    cfg = ImageManipConfig()
    size = Size2f(SCENE_SIZE[0], SCENE_SIZE[1])

    def average_filter(x, y):
        x_arr.append(x)
        y_arr.append(y)
        if AVG_MAX_NUM < len(x_arr):
            x_arr.pop(0)
        if AVG_MAX_NUM < len(y_arr):
            y_arr.pop(0)
        x_avg = 0
        y_avg = 0
        for i in range(len(x_arr)):
            x_avg += x_arr[i]
            y_avg += y_arr[i]
        x_avg = x_avg / len(x_arr)
        y_avg = y_avg / len(y_arr)
        if x_avg < limits[0]:
            x_avg = limits[0]
        if y_avg < limits[1]:
            y_avg = limits[1]
        if limits[2] < x_avg:
            x_avg = limits[2]
        if limits[3] < y_avg:
            y_avg = limits[3]
        return x_avg, y_avg

    while True:
        dets = node.io['dets'].get().detections
        if len(dets) == 0:
            continue

        coords = dets[0]  # take first
        # Get detection center
        x = (coords.xmin + coords.xmax) / 2 * ORIGINAL_SIZE[0]
        y = (coords.ymin + coords.ymax) / 2 * ORIGINAL_SIZE[1] + 100

        x_avg, y_avg = average_filter(x, y)

        rect = RotatedRect()
        rect.size = size
        rect.center = Point2f(x_avg, y_avg)
        cfg.setCropRotatedRect(rect, False)
        # MJPEG output for UVC consumption
        cfg.setFrameType(ImgFrame.Type.NV12)
        node.io['cfg'].send(cfg)


# I am 100% sure this is a gross way to do it, but this does work!
# inspect.getSource(onboardScripting) returns the text of the function, which we then split to remove the first 'def' line
# The result is a list, which we join() to an empty string. To be honest I am not confident this is needed.
# The result of the split has leading whitespace, which we remove with textwrap.dedent.
# Finally, the string looks basically like a python file, and is ready to upload to the script node.
processedOnBoardScriptString = textwrap.dedent(
    "".join(inspect.getsource(onboardScripting).split("\n", 1)[1:]))


def makeOnboardScript():
    # Script node for onboard cropping based on NN border
    script = pipeline.create(dai.node.Script)
    mobilenet.out.link(script.inputs['dets'])
    script.setScript(processedOnBoardScriptString)
    return script


script = makeOnboardScript()
crop_manip = pipeline.create(dai.node.ImageManip)
crop_manip.setMaxOutputFrameSize(3110400)
crop_manip.initialConfig.setResize(1920, 1080)  # UVC wants a 1080P frame
crop_manip.initialConfig.setFrameType(
    dai.RawImgFrame.Type.NV12)  # MJPEG output for UVC consumption

script.outputs['cfg'].link(crop_manip.inputConfig)
cam_rgb.isp.link(crop_manip.inputImage)

# Create an UVC (USB Video Class) output node. It needs 1920x1080, NV12 input
uvc = pipeline.create(dai.node.UVC)
# videoEnc.bitstream.link(uvc.input)
crop_manip.out.link(uvc.input)

# Terminate app handler
quitEvent = threading.Event()
signal.signal(signal.SIGTERM, lambda *_args: quitEvent.set())

# Pipeline defined, now the device is connected to
with dai.Device(pipeline, usb2Mode=False) as device:
    if device.getDeviceInfo().protocol == dai.XLinkProtocol.X_LINK_USB_VSC and device.getUsbSpeed() not in (dai.UsbSpeed.SUPER, dai.UsbSpeed.SUPER_PLUS):
        print("Sorry, USB2 link speed not working, see default depthai UVC app", file=sys.stderr)
        raise SystemExit(1)

    print("\nDevice started, please keep this process running")
    print("and open an UVC viewer. Example on Linux:")
    print("    guvcview -d /dev/video0")
    print("\nTo close: Ctrl+C")

    # Doing nothing here, just keeping the host feeding the watchdog
    while not quitEvent.is_set():
        try:
            time.sleep(0.1)
        except KeyboardInterrupt:
            break
