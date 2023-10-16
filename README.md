# Facetracking Webcam

Goal: use a Luxonis camera such as the [OAK-D Lite](https://shop.luxonis.com/collections/oak-cameras-1/products/oak-d-lite-1) as a UVC webcam, locating the user's face with a neural net and cropping the output stream to focus on that face.

The OAK-D Lite has a 4K color camera, programmable image pipelines, and supports a UVC mode which has a maximum resolution of 1080p. This lines up nicely. Any of the Luxonis USB cameras with 4k color sensors should work without code changes, and others are likely to be adaptable as well.

Using a *MobileNetDetectionNetwork* Node with a model from the OpenVINO model zoo ([face-detection-retail-0004](https://docs.openvino.ai/2023.1/omz_models_model_face_detection_retail_0004.html) selected arbitrarily) we can process 300x300px downsamples from the color feed and get back at least one face. With on-camera python scripting running in a Script Node, we parse the location of the detected face, and determine a 1080p frame centered on that face, with limits to prevent extending outside the original image. The script node does a little math to running-average the [x,y] values and sets the configuration of a *ImageManip* Node, which crops the input frame and outputs the result to a UVC Node, which creates the stream ingested by the host just like any other USB connected webcam.

The layout of these pipelines is like this:
```
 ┌───────────────┐      ┌─────────────────┐  ┌────────────────┐  ┌────────────────┐
 │4k Color Camera├─┬───►│300x300px Preview├─►│MobileNet       ├─►│On-Device Script├─┐
 │               │ │    │                 │  │   Detection    │  │                │ │
 └───────────────┘ │    └─────────────────┘  │      Network   │  │                │ │
                   │                         │(face detection)│  │                │ │
                   │                         └────────────────┘  └────────────────┘ │
                   │                                                                │
                   │  ┌─────────────────────────────────────────────────────────────┘
                   │  │
                   │  │ ┌─────────────────┐  ┌────────────┐
                   │  └►│ImageManip Node  ├─►│UVC Node    │
                   │    │                 │  │ Webcam     │
                   └───►│                 │  │            │
                        └─────────────────┘  └────────────┘

```
References:
* [UVC Node demo app](https://github.com/luxonis/depthai/tree/main/apps/uvc)
* [gen2-lossless-zooming demo](https://github.com/luxonis/depthai-experiments/tree/master/gen2-lossless-zooming)
