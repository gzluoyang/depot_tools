@echo off
:: Copyright 2020 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

:: To revert a recent deployment of this executable, revert the corresponding
:: CL in cipd_manifest.txt.

call "%~dp0\cipd_bin_setup.bat" > nul 2>&1
"%~dp0\.cipd_bin\dirmd.exe" %*
