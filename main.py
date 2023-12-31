#!/usr/bin/env python3

"""
Goal: using the 4k full-isp color camera, automatically crop a 1080P image and feed that to the host using UVC.
- on the way, use a face tracking NN and center the crop on whichever face is detected first

We need a couple different nodes:
- cam_rgb is a dai.node.ColorCamera
- mobilenet is a dai.node.MobileNetDetectionNetwork
- crop_manip is an dai.node.ImageManip
- script is a dai.node.Script
- uvc is a dai.node.UVC

The layout of these pipelines is like this:
cam_rgb.preview[300,300] -> mobilenet -> script -> crop_manip configuration
cam_rgb -> crop_manip -> uvc

For reducing jitter a bit, the values coming from the face detection NN will be running-averaged.
"""

try:
    import depthai as dai
    import blobconverter
    import inspect
    import platform
    import textwrap
    import sys
    import signal
    import threading
    import time
except ImportError as e:
    print(e, f"""
did you remember to source (or create) a venv with the dependencies?
e.g.
source depthai_venv/bin/activate.fish
pip3 install -r requirements.txt
""")
    raise SystemExit(1)

# root pipeline definition
pipeline = dai.Pipeline()

# source for all inputs is the 4k color camera
cam_rgb = pipeline.create(dai.node.ColorCamera)
cam_rgb.setBoardSocket(dai.CameraBoardSocket.RGB)
cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_4_K)
# highly cinematic! Maximum framerate at 4k seems to be "28.86" which is a weird number but OK
cam_rgb.setFps(24)
#cam_rgb.initialControl.setManualFocus(130)
cam_rgb.setInterleaved(False)

# set the preview to squash the full frame down to 300x300 for the mobilenet input
cam_rgb.setPreviewKeepAspectRatio(False)
cam_rgb.setPreviewSize(300, 300)

# Create MobileNet detection network
mobilenet = pipeline.create(dai.node.MobileNetDetectionNetwork)
mobilenet.setBlobPath(blobconverter.from_zoo(
    name="face-detection-retail-0004", shaves=5))
mobilenet.setConfidenceThreshold(0.7)
cam_rgb.preview.link(mobilenet.input)


def onboardScripting():
    # This code is never used by the host, it is uploaded to a script node on the device
    # Slight shennanigans are used to make this editable with all the nicities of a normal IDE, but it's uploaded as a raw string
    ISP_SIZE = (3840, 2160)  # 4K
    OUTPUT_SIZE = (1920, 1080)  # 1080P

    # The maximum and minimum center values prevent the output rectangle from overlapping the input rectangle
    xMin = OUTPUT_SIZE[0]//2
    xMax = ISP_SIZE[0] - OUTPUT_SIZE[0]/2
    yMin = OUTPUT_SIZE[1]//2
    yMax = ISP_SIZE[1] - OUTPUT_SIZE[1]/2
    output_size = Size2f(OUTPUT_SIZE[0], OUTPUT_SIZE[1])
    cfg = ImageManipConfig()

    def clamp(minv, maxv, inputv):
        return max(min(maxv, inputv), minv)

    x_arr = []
    y_arr = []
    AVG_MAX_NUM = 7

    def average_filter(x, y):
        # we keep a running average of values for x and y to reduce jittering
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
        x_avg = int(x_avg / len(x_arr))
        y_avg = int(y_avg / len(y_arr))
        # kinda wish there was a builtin for this sort of operation
        return x_avg, y_avg

    while True:
        x = 0
        y = 0
        dets = node.io['dets'].get().detections
        if len(dets) == 0:
            x = ISP_SIZE[0]/2
            y - ISP_SIZE[1]/2
        else:
            # at least one face was detected, so let's center a frame on it/them
            # we find the bounding box that captures as many faces as our NN returns
            detection_bounds = {"xMin":1, "xMax": 0, "yMin":1, "yMax":0}
            for face in dets:
                detection_bounds["xMin"] = min(detection_bounds["xMin"], face.xmin)
                detection_bounds["xMax"] = max(detection_bounds["xMax"], face.xmax)
                detection_bounds["yMin"] = min(detection_bounds["yMin"], face.ymin)
                detection_bounds["yMax"] = max(detection_bounds["yMax"], face.ymax)
            # Get detection center, coords values are normalized to (0,1)
            x = int((detection_bounds["xMin"] + detection_bounds["xMax"]) / 2 * ISP_SIZE[0])
            y = int((detection_bounds["yMin"] + detection_bounds["yMax"]) / 2 * ISP_SIZE[1])

        # we limit the input range to keep the crop view inside the original frame size
        x_clamped = clamp(xMin, xMax, x)
        y_clamped = clamp(yMin, yMax, y)
        x_avg, y_avg = average_filter(x_clamped, y_clamped)

        # node.warn(f"coords x:{x} y:{y} AVERAGE x:{x_avg} y:{y_avg}")

        crop_rect = RotatedRect()
        crop_rect.size = output_size
        crop_rect.center = Point2f(x_avg, y_avg)
        cfg.setCropRotatedRect(crop_rect, False)
        # NV12 output for UVC consumption
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
# crop_manip.initialConfig.setHorizontalFlip(False)
crop_manip.initialConfig.setResize(1920, 1080)  # UVC wants a 1080P frame
crop_manip.initialConfig.setFrameType(
    dai.RawImgFrame.Type.NV12)  # NV12 output for UVC consumption

script.outputs['cfg'].link(crop_manip.inputConfig)
cam_rgb.isp.link(crop_manip.inputImage)

# Create an UVC (USB Video Class) output node. It needs 1920x1080, NV12 input
uvc = pipeline.create(dai.node.UVC)
crop_manip.out.link(uvc.input)

# Terminate app handler
quitEvent = threading.Event()
signal.signal(signal.SIGTERM, lambda *_args: quitEvent.set())

# Pipeline defined, get the device - but not in USB2 mode, because that has issuse with UVC.
with dai.Device(pipeline, usb2Mode=False) as device:
    if device.getDeviceInfo().protocol == dai.XLinkProtocol.X_LINK_USB_VSC and device.getUsbSpeed() not in (dai.UsbSpeed.SUPER, dai.UsbSpeed.SUPER_PLUS):
        print("Sorry, USB2 link speed not working, see default depthai UVC app", file=sys.stderr)
        raise SystemExit(1)

    # we want to suppress the excessive number "[error] Still capture ignored, as the output is not connected"
    device.setLogLevel(dai.LogLevel.WARN)
    device.setLogOutputLevel(dai.LogLevel.WARN)

    print("\nDevice started, please keep this process running")
    print("and open an UVC viewer. Example on Linux:")
    print("    guvcview -d /dev/video0")
    print("\nTo close: Ctrl+C")

    # Periodically log useful stats, but otherwise do nothing. Sleeping too long fails to pet a watchdog
    # TODO: determine if getting stats interrupts anything
    lastLogTime = time.monotonic_ns()
    while not quitEvent.is_set():
        try:
            currentTime = time.monotonic_ns()
            if (currentTime - lastLogTime > 10000000000):
                temperature = device.getChipTemperature()
                print(f'{currentTime}: temp {temperature.average}')
                # one of the demos does this and throws an error if temperature is over 100 -- that seems too high, how about 80?
                # the depthai API enforces thermal shutdown when the CPU is at 105 C, but the camera images get noisy before that.
                # reference: https://docs.luxonis.com/projects/hardware/en/latest/pages/articles/lite_temp_test/#oak-d-lite-temperature-tests
                if any(map(lambda field: getattr(temperature, field) > 80, ["average", "css", "dss", "mss", "upa"])):
                    raise RuntimeError("Over temp error!")
                lastLogTime = currentTime
            time.sleep(0.1)
        except KeyboardInterrupt:
            break
