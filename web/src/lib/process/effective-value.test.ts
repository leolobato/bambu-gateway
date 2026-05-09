import { describe, it, expect } from 'vitest';
import { displayValue, effectiveValue, revertTarget } from './effective-value';
import type { ProcessOption, ProcessOptionsCatalogue, ProcessModifications } from './types';

const catalogue: ProcessOptionsCatalogue = {
  version: 'v1',
  options: {
    layer_height: {
      key: 'layer_height', label: 'Layer height', category: 'quality',
      tooltip: '', type: 'coFloat', sidetext: 'mm',
      default: '0.20', min: 0.05, max: 0.75,
      enumValues: null, enumLabels: null,
      mode: 'simple', guiType: '', nullable: false, readonly: false,
    },
    sparse_infill_density: {
      key: 'sparse_infill_density', label: 'Sparse infill density', category: 'strength',
      tooltip: '', type: 'coPercent', sidetext: '%',
      default: '15%', min: 0, max: 100,
      enumValues: null, enumLabels: null,
      mode: 'simple', guiType: '', nullable: false, readonly: false,
    },
  },
};

const modifications: ProcessModifications = {
  processSettingId: '0.16mm Standard @P1P',
  modifiedKeys: ['layer_height'],
  values: { layer_height: '0.16' },
};

const baseline: Record<string, string> = {
  layer_height: '0.20',
  sparse_infill_density: '15%',
  top_shell_layers: '4',
};

describe('effectiveValue', () => {
  it('prefers user override over everything else', () => {
    expect(effectiveValue('layer_height', { layer_height: '0.12' }, modifications, baseline, catalogue))
      .toBe('0.12');
  });

  it('falls back to 3MF modification when no override', () => {
    expect(effectiveValue('layer_height', {}, modifications, baseline, catalogue)).toBe('0.16');
  });

  it('falls back to baseline when key not modified by file', () => {
    expect(effectiveValue('sparse_infill_density', {}, modifications, baseline, catalogue)).toBe('15%');
  });

  it('falls back to catalogue default when baseline missing', () => {
    expect(effectiveValue('layer_height', {}, null, {}, catalogue)).toBe('0.20');
  });

  it('returns null when key is unknown everywhere', () => {
    expect(effectiveValue('mystery_key', {}, null, {}, catalogue)).toBeNull();
  });

  it('treats null modifications as absent', () => {
    expect(effectiveValue('sparse_infill_density', {}, null, baseline, catalogue)).toBe('15%');
  });
});

describe('displayValue', () => {
  const brimTypeOption: ProcessOption = {
    key: 'brim_type', label: 'Brim type', category: 'quality',
    tooltip: '', type: 'coEnum', sidetext: '',
    default: 'auto_brim',
    min: null, max: null,
    enumValues: ['auto_brim', 'no_brim', 'outer_only'],
    enumLabels: ['Auto', 'No brim', 'Outer only'],
    mode: 'simple', guiType: '', nullable: false, readonly: false,
  };

  it('maps an enum value to its catalogue label', () => {
    expect(displayValue('no_brim', brimTypeOption)).toBe('No brim');
  });

  it('returns the raw value for non-enum options', () => {
    const layerHeight = catalogue.options.layer_height;
    expect(displayValue('0.16', layerHeight)).toBe('0.16');
  });

  it('returns the raw value when the enum option lacks labels', () => {
    const partial: ProcessOption = { ...brimTypeOption, enumLabels: null };
    expect(displayValue('no_brim', partial)).toBe('no_brim');
  });

  it('returns the raw value when the value is not in enum_values', () => {
    expect(displayValue('mystery', brimTypeOption)).toBe('mystery');
  });

  it('returns the raw value when option is null/undefined', () => {
    expect(displayValue('no_brim', null)).toBe('no_brim');
    expect(displayValue('no_brim', undefined)).toBe('no_brim');
  });
});

describe('revertTarget', () => {
  it('uses the 3MF value when modified by file', () => {
    expect(revertTarget('layer_height', modifications, baseline, catalogue)).toBe('0.16');
  });

  it('uses the baseline when not modified by file', () => {
    expect(revertTarget('sparse_infill_density', modifications, baseline, catalogue)).toBe('15%');
  });

  it('falls back to catalogue default', () => {
    expect(revertTarget('layer_height', null, {}, catalogue)).toBe('0.20');
  });

  it('ignores user overrides — revert is what we revert *to*', () => {
    // Even though the user had picked something, the revert target is still the 3MF/default value.
    expect(revertTarget('layer_height', modifications, baseline, catalogue)).toBe('0.16');
  });
});
