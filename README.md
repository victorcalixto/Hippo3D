# Hippo3D

<p align="center">
  <img src="./icons/hippo3d_logo.png" width="320" alt="Hippo3D Logo">
</p>

<p align="center">
  <strong>Free and Open Source Modeling Tools for Designers</strong>
</p>

<p align="center">
  CAD-style workflows for Blender
</p>

---

# About

Hippo3D is a free and open-source CAD-style modeling toolkit for Blender focused on precision modeling workflows for designers, architects, computational designers, makers, digital fabrication, and advanced geometry workflows.

The project is inspired by Rhino 3D and aims to bring Rhino-like direct modeling interactions and CAD workflows into Blender while remaining fully integrated with Blender’s ecosystem and Python API.

Hippo3D combines:

- CAD-style interaction
- Blender viewport integration
- NURBS workflows
- Surface-Like modeling
- Construction planes
- Command-line workflows
- Open-source extensibility
- Computational design experimentation

---

# Features

## Curve Tools

- Line
- Polyline
- Rectangle
- Circle
- Arc
- Ellipse
- Polygon
- XLine
- NURBS Curves

## Surface-Like Tools

- Loft
- Revolve
- Pipe
- Extrude
- Planar Surface
- Edge Surface

## CAD Utilities

- Join
- Explode
- Offset
- Trim
- Array
- Project
- Convert to Mesh

## Construction Plane System

- Saved CPlanes
- 3 Point CPlane
- Face-aligned CPlane
- Curve Perpendicular CPlane
- CPlane Rotation
- View ↔ CPlane alignment

## Command Workflow

Hippo3D includes a CAD-style command system directly inside Blender.

Example workflow:

- `Ctrl + /` → Open command mode
- Type commands directly in the viewport
- Press `Enter` to execute

---

# Screenshots

<p align="center">
  <img src="https://github-production-user-asset-6210df.s3.amazonaws.com/4661824/599141907-aa9de11c-813c-46e7-9a99-1dd40023b014.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVCODYLSA53PQK4ZA%2F20260528%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260528T012317Z&X-Amz-Expires=300&X-Amz-Signature=6ca021459dc93c2f9d7a27f81f7df0e266083e00cdd1f92e8f6a798f3622416e&X-Amz-SignedHeaders=host&response-content-type=image%2Fgif" width="700">
</p>

<p align="center">
  <img src="https://github-production-user-asset-6210df.s3.amazonaws.com/4661824/599143751-77dc066f-4574-4d66-a35a-f8761bab920e.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVCODYLSA53PQK4ZA%2F20260528%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260528T013034Z&X-Amz-Expires=300&X-Amz-Signature=4fa5902c7bf7bc14de4950fccc06ba478f73ebeff1cd8379ec5c97cc5c7de617&X-Amz-SignedHeaders=host&response-content-type=image%2Fgif" width="700">
</p>

<p align="center">
  <img src="https://github-production-user-asset-6210df.s3.amazonaws.com/4661824/599143081-7826ce0c-5a38-4848-8825-23efa2aee23c.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVCODYLSA53PQK4ZA%2F20260528%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260528T012637Z&X-Amz-Expires=300&X-Amz-Signature=6def3c24580c2a3adc50dbbf1b0c8e1b5d74956d5887fae5200d4e34640c6641&X-Amz-SignedHeaders=host&response-content-type=image%2Fgif" width="700">
</p>


<p align="center">
  <img src="https://github-production-user-asset-6210df.s3.amazonaws.com/4661824/599142474-eb8fb291-6a82-4d30-a406-b017c0371207.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVCODYLSA53PQK4ZA%2F20260528%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260528T012450Z&X-Amz-Expires=300&X-Amz-Signature=214b9ee7a55bbe663c6e526ce4eceb27954226cc923e38ab1eefd58a96a68ba3&X-Amz-SignedHeaders=host&response-content-type=image%2Fgif" width="700">
</p>


<p align="center">
  <img src="https://github-production-user-asset-6210df.s3.amazonaws.com/4661824/599142317-5179bdce-04c9-4659-85f8-0234b07c0309.gif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVCODYLSA53PQK4ZA%2F20260528%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260528T012417Z&X-Amz-Expires=300&X-Amz-Signature=0734c1c3b2fee51655d7e1bc7f7c7719674aa604eb370f23e53cb4e8dc1ce725&X-Amz-SignedHeaders=host&response-content-type=image%2Fgif" width="700">
</p>

---

# Installation

## Blender Add-on Installation

1. Download the repository:

```bash
git clone https://github.com/YOUR_USERNAME/Hippo3D.git
```

2. Open Blender

3. Go to:

```text
Edit → Preferences → Add-ons
```

4. Click:

```text
Install...
```

5. Select the ZIP file

6. Enable:

```text
Hippo3D
```

---

# Goals

Hippo3D aims to provide:

- Open-source CAD workflows inside Blender
- Professional modeling tools for designers
- A bridge between CAD and DCC workflows
- Computational design integration trough Sverchok and Geometry Nodes
- A developer-friendly Python architecture

---

# Philosophy

Hippo3D is inspired by:

- Rhino 3D
- Blender
- Bonsai
- OpenCascade
- CAD workflows
- Computational design
- Free/Open Source software ecosystems

The project embraces openness, extensibility, experimentation, and interoperability.

---

# Roadmap

- [ ] Improved NURBS workflows
- [ ] Better snapping system
- [ ] SVG/DXF interoperability
- [ ] BIM interoperability
- [ ] Geometry kernel experiments
- [ ] Computational design workflows

---

# Contributing

Contributions are welcome.

Areas of interest include:

- CAD interaction design
- Geometry kernels
- NURBS
- Snapping systems
- UX/UI
- Computational geometry
- Parametric workflows
- Blender Python API development

---

# License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).

```text
Hippo3D
Copyright (C) 2026 Victor Calixto

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See the GNU General Public License for more details.
```
---

# Author

Victor Calixto

