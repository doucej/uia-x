#!/usr/bin/env swift
// ocr_region.swift — Vision-based OCR for a screen region.
// Usage: swift ocr_region.swift <x> <y> <w> <h>
// Outputs JSON: {"lines": ["text1", "text2", ...]}
//
// Used by macos_impl.py to read Quicken's inline split editor rows,
// which are rendered visually but not exposed in the AX accessibility tree.

import Foundation
import Vision
import CoreGraphics

let args = CommandLine.arguments
guard args.count >= 5,
      let x = Double(args[1]),
      let y = Double(args[2]),
      let w = Double(args[3]),
      let h = Double(args[4]),
      w > 0, h > 0
else {
    print("{\"error\": \"Usage: ocr_region x y w h\"}")
    exit(1)
}

let displayID = CGMainDisplayID()
let bounds = CGRect(x: x, y: y, width: w, height: h)

guard let cgImage = CGDisplayCreateImage(displayID, rect: bounds) else {
    print("{\"error\": \"Failed to capture screen region\"}")
    exit(1)
}

let semaphore = DispatchSemaphore(value: 0)
var recognizedLines: [String] = []

let request = VNRecognizeTextRequest { req, err in
    defer { semaphore.signal() }
    guard err == nil,
          let observations = req.results as? [VNRecognizedTextObservation]
    else { return }

    // Sort top-to-bottom (VN bounding boxes are normalized with origin bottom-left)
    let sorted = observations.sorted { $0.boundingBox.minY > $1.boundingBox.minY }
    recognizedLines = sorted.compactMap { $0.topCandidates(1).first?.string }
}

request.recognitionLevel = .accurate
request.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try? handler.perform([request])
semaphore.wait()

let output: [String: Any] = ["lines": recognizedLines]
if let data = try? JSONSerialization.data(withJSONObject: output),
   let json = String(data: data, encoding: .utf8) {
    print(json)
} else {
    print("{\"lines\": []}")
}
