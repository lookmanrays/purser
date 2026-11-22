package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func findPackageCoverage(t *testing.T, cov map[string]*PackageCoverage, pkgSuffix string) *PackageCoverage {
	t.Helper()
	for k, v := range cov {
		if strings.HasSuffix(k, pkgSuffix) || k == pkgSuffix {
			return v
		}
	}
	t.Fatalf("package matching suffix %q not found in coverage map: %+v", pkgSuffix, cov)
	return nil
}

func TestAC1_CovercheckFailsWhenPackageBelowFloor(t *testing.T) {
	defer guard(t)

	configPath := testdataPath(t, filepath.Join("configs", "config_valid.yaml"))

	tests := []struct {
		name            string
		profile         string
		expectedPackage string
	}{
		{
			name:            "kernel package below 85 percent floor",
			profile:         "cover_kernel_fail.out",
			expectedPackage: "internal/kernel/store",
		},
		{
			name:            "default package below 70 percent floor",
			profile:         "cover_default_fail.out",
			expectedPackage: "tools/covercheck",
		},
		{
			name:            "package below 90 percent override floor",
			profile:         "cover_override_fail.out",
			expectedPackage: "internal/kernel/codec",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			defer guard(t)

			profilePath := testdataPath(t, filepath.Join("profiles", tc.profile))
			violations, err := Check(profilePath, configPath)
			if err != nil {
				t.Fatalf("Check returned unexpected error: %v", err)
			}
			if len(violations) == 0 {
				t.Fatalf("expected violation for %s, got none", tc.profile)
			}

			found := false
			for _, v := range violations {
				if strings.Contains(v, tc.expectedPackage) {
					found = true
					break
				}
			}
			if !found {
				t.Fatalf("expected violation naming package %q, got: %v", tc.expectedPackage, violations)
			}
		})
	}

	t.Run("all packages meet floor", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_pass.out"))
		violations, err := Check(profilePath, configPath)
		if err != nil {
			t.Fatalf("Check returned unexpected error: %v", err)
		}
		if len(violations) != 0 {
			t.Fatalf("expected 0 violations for passing profile, got: %v", violations)
		}
	})

	t.Run("package exactly at its floor passes", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_exact_floor.out"))
		violations, err := Check(profilePath, configPath)
		if err != nil {
			t.Fatalf("Check returned unexpected error: %v", err)
		}
		if len(violations) != 0 {
			t.Fatalf("expected 0 violations when packages sit exactly at floor, got: %v", violations)
		}
	})

	t.Run("excluded package is never reported even at zero percent", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_excluded.out"))
		violations, err := Check(profilePath, configPath)
		if err != nil {
			t.Fatalf("Check returned unexpected error: %v", err)
		}
		for _, v := range violations {
			if strings.Contains(v, "cmd/purser") {
				t.Fatalf("excluded package cmd/purser must never be reported, got violation: %s", v)
			}
		}
		if len(violations) != 0 {
			t.Fatalf("expected 0 violations for profile with only excluded package failing, got: %v", violations)
		}
	})
}

func TestAC1_CovercheckProfileNegativeSpace(t *testing.T) {
	defer guard(t)

	configPath := testdataPath(t, filepath.Join("configs", "config_valid.yaml"))

	t.Run("repeated blocks counted once and covered if any copy has count above zero", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_repeated_blocks.out"))
		violations, err := Check(profilePath, configPath)
		if err != nil {
			t.Fatalf("Check returned unexpected error: %v", err)
		}
		if len(violations) != 0 {
			t.Fatalf("expected repeated blocks profile to pass without violations, got: %v", violations)
		}

		cov, err := ParseCoverprofile(profilePath)
		if err != nil {
			t.Fatalf("ParseCoverprofile failed: %v", err)
		}
		storeCov := findPackageCoverage(t, cov, "internal/kernel/store")
		if storeCov.TotalStatements != 10 {
			t.Fatalf("expected TotalStatements 10 for deduplicated repeated blocks, got %d", storeCov.TotalStatements)
		}
		if storeCov.CoveredStatements != 9 {
			t.Fatalf("expected CoveredStatements 9 (neither first-copy nor last-copy wins), got %d", storeCov.CoveredStatements)
		}
		if storeCov.Percentage != 90.0 {
			t.Fatalf("expected Percentage 90.0, got %v", storeCov.Percentage)
		}
	})

	t.Run("atomic mode with counts above one", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_atomic.out"))
		violations, err := Check(profilePath, configPath)
		if err != nil {
			t.Fatalf("Check returned unexpected error: %v", err)
		}
		if len(violations) != 0 {
			t.Fatalf("expected atomic mode profile to pass without violations, got: %v", violations)
		}

		cov, err := ParseCoverprofile(profilePath)
		if err != nil {
			t.Fatalf("ParseCoverprofile failed: %v", err)
		}
		storeCov := findPackageCoverage(t, cov, "internal/kernel/store")
		if storeCov.TotalStatements != 20 {
			t.Fatalf("expected TotalStatements 20 for atomic mode, got %d", storeCov.TotalStatements)
		}
		if storeCov.CoveredStatements != 18 {
			t.Fatalf("expected CoveredStatements 18 for atomic mode (not count-weighted), got %d", storeCov.CoveredStatements)
		}
		if storeCov.Percentage != 90.0 {
			t.Fatalf("expected Percentage 90.0, got %v", storeCov.Percentage)
		}
	})

	t.Run("malformed line in coverprofile returns error", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_malformed_line.out"))
		_, err := Check(profilePath, configPath)
		if err == nil {
			t.Fatal("expected error on malformed line in coverprofile, got nil")
		}
	})

	t.Run("missing mode header returns error", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_missing_mode.out"))
		_, err := Check(profilePath, configPath)
		if err == nil {
			t.Fatal("expected error on missing mode header in coverprofile, got nil")
		}
	})

	t.Run("unknown mode header returns error", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_unknown_mode.out"))
		_, err := Check(profilePath, configPath)
		if err == nil {
			t.Fatal("expected error on unknown mode header in coverprofile, got nil")
		}
	})

	t.Run("profile with no blocks returns error", func(t *testing.T) {
		defer guard(t)

		profilePath := testdataPath(t, filepath.Join("profiles", "cover_empty.out"))
		_, err := Check(profilePath, configPath)
		if err == nil {
			t.Fatal("expected error on empty coverprofile with no blocks, got nil")
		}
	})
}

func TestAC1_CovercheckCLIFailsBelowFloor(t *testing.T) {
	defer guard(t)

	configPath := testdataPath(t, filepath.Join("configs", "config_valid.yaml"))
	failProfile := testdataPath(t, filepath.Join("profiles", "cover_kernel_fail.out"))
	passProfile := testdataPath(t, filepath.Join("profiles", "cover_pass.out"))

	var stdout, stderr bytes.Buffer
	code := Run([]string{"--coverprofile", failProfile, "--config", configPath}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("expected exit code 1 for failing coverage profile, got %d", code)
	}
	output := stdout.String() + stderr.String()
	if !strings.Contains(output, "internal/kernel/store") {
		t.Fatalf("expected CLI output to name failing package internal/kernel/store, got: %s", output)
	}

	stdout.Reset()
	stderr.Reset()
	code = Run([]string{"--coverprofile", passProfile, "--config", configPath}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for passing coverage profile, got %d (output: %s)", code, stdout.String()+stderr.String())
	}
}

func TestAC1_CheckedInCoverageConfig(t *testing.T) {
	defer guard(t)

	configPath := filepath.Join(findRepoRoot(t), ".coverage.yaml")
	cfg, err := LoadConfig(configPath)
	if err != nil {
		t.Fatalf("failed to load checked-in .coverage.yaml: %v", err)
	}
	if cfg.KernelFloor != 85.0 {
		t.Fatalf("expected KernelFloor 85.0, got %v", cfg.KernelFloor)
	}
	if cfg.DefaultFloor != 70.0 {
		t.Fatalf("expected DefaultFloor 70.0, got %v", cfg.DefaultFloor)
	}
	if len(cfg.Exclusions) == 0 {
		t.Fatal("expected at least one exclusion in checked-in .coverage.yaml (cmd/purser)")
	}

	foundPurser := false
	for _, exc := range cfg.Exclusions {
		if exc.Package == "" {
			t.Fatal("exclusion has empty package name")
		}
		if strings.TrimSpace(exc.Reason) == "" {
			t.Fatalf("exclusion for package %q must have a non-empty reason", exc.Package)
		}
		if strings.Contains(exc.Package, "cmd/purser") {
			foundPurser = true
		}
	}
	if !foundPurser {
		t.Fatal("expected cmd/purser to be excluded in checked-in .coverage.yaml")
	}
}

func TestAC2_FloorOverrideBelowDefaultRejected(t *testing.T) {
	defer guard(t)

	tests := []struct {
		name        string
		configFile  string
		expectedPkg string
	}{
		{
			name:        "kernel floor override below 85 percent rejected",
			configFile:  "config_invalid_kernel.yaml",
			expectedPkg: "internal/kernel/codec",
		},
		{
			name:        "default floor override below 70 percent rejected",
			configFile:  "config_invalid_default.yaml",
			expectedPkg: "tools/covercheck",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			defer guard(t)

			path := testdataPath(t, filepath.Join("configs", tc.configFile))
			cfg, err := LoadConfig(path)
			if err == nil {
				t.Fatalf("expected LoadConfig to reject override below default in %s, but got cfg: %+v", tc.configFile, cfg)
			}
			if !strings.Contains(err.Error(), tc.expectedPkg) {
				t.Fatalf("expected error naming package %q, got: %v", tc.expectedPkg, err)
			}

			passProfile := testdataPath(t, filepath.Join("profiles", "cover_pass.out"))
			_, checkErr := Check(passProfile, path)
			if checkErr == nil {
				t.Fatalf("expected Check to return error on config with lowered override")
			}
		})
	}

	t.Run("unknown key in config is rejected", func(t *testing.T) {
		defer guard(t)

		path := testdataPath(t, filepath.Join("configs", "config_unknown_key.yaml"))
		_, err := LoadConfig(path)
		if err == nil {
			t.Fatal("expected LoadConfig to reject unknown key in config, got nil error")
		}
	})

	t.Run("floor outside 0 to 100 is rejected", func(t *testing.T) {
		defer guard(t)

		path := testdataPath(t, filepath.Join("configs", "config_floor_out_of_range.yaml"))
		_, err := LoadConfig(path)
		if err == nil {
			t.Fatal("expected LoadConfig to reject floor outside 0-100, got nil error")
		}
	})

	t.Run("exclusion without reason is rejected", func(t *testing.T) {
		defer guard(t)

		path := testdataPath(t, filepath.Join("configs", "config_exclusion_no_reason.yaml"))
		_, err := LoadConfig(path)
		if err == nil {
			t.Fatal("expected LoadConfig to reject exclusion without reason, got nil error")
		}
	})

	t.Run("valid override raising floor is accepted", func(t *testing.T) {
		defer guard(t)

		path := testdataPath(t, filepath.Join("configs", "config_valid.yaml"))
		cfg, err := LoadConfig(path)
		if err != nil {
			t.Fatalf("expected LoadConfig to accept valid override, got error: %v", err)
		}
		if cfg == nil {
			t.Fatal("expected non-nil config")
		}
	})
}

func TestAC2_CovercheckCLIRejectsInvalidOverride(t *testing.T) {
	defer guard(t)

	invalidConfig := testdataPath(t, filepath.Join("configs", "config_invalid_kernel.yaml"))
	passProfile := testdataPath(t, filepath.Join("profiles", "cover_pass.out"))

	var stdout, stderr bytes.Buffer
	code := Run([]string{"--coverprofile", passProfile, "--config", invalidConfig}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("expected exit code 1 when config has invalid override, got %d", code)
	}
	output := stdout.String() + stderr.String()
	if !strings.Contains(output, "internal/kernel/codec") {
		t.Fatalf("expected CLI output to name failing package internal/kernel/codec, got: %s", output)
	}
}
