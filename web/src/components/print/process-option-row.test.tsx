import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProcessOptionRow } from './process-option-row';
import type { ProcessOption } from '@/lib/process/types';

const layerHeight: ProcessOption = {
  key: 'layer_height', label: 'Layer height', category: 'quality',
  tooltip: 'Distance between the layers, controls vertical resolution.',
  type: 'coFloat', sidetext: 'mm',
  default: '0.20', min: 0.05, max: 0.75,
  enumValues: null, enumLabels: null,
  mode: 'simple', guiType: '', nullable: false, readonly: false,
};

const enableSupport: ProcessOption = {
  ...layerHeight,
  key: 'enable_support', label: 'Enable support', tooltip: '',
  type: 'coBool', sidetext: '', default: '0',
  min: null, max: null,
};

describe('ProcessOptionRow', () => {
  it('renders the label, value, and sidetext', () => {
    render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.16"
        revertTo="0.20"
        isUserEdited={false}
        isFileModified
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByText('Layer height')).toBeInTheDocument();
    expect(screen.getByText('0.16')).toBeInTheDocument();
    expect(screen.getByText('mm')).toBeInTheDocument();
  });

  it('shows the orange dot when user-edited', () => {
    const { container } = render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.12"
        revertTo="0.16"
        isUserEdited
        isFileModified
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(container.querySelector('.bg-orange-500')).toBeInTheDocument();
    expect(container.querySelector('.bg-sky-500')).not.toBeInTheDocument();
  });

  it('toggles expand on click', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <ProcessOptionRow
        option={enableSupport}
        value="0"
        revertTo="0"
        isUserEdited={false}
        isFileModified={false}
        showTooltipCaption={false}
        isExpanded={false}
        onToggleExpand={onToggle}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    await user.click(screen.getByRole('button', { name: /Enable support/ }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('reveals the editor when expanded', () => {
    render(
      <ProcessOptionRow
        option={enableSupport}
        value="1"
        revertTo="0"
        isUserEdited
        isFileModified={false}
        showTooltipCaption={false}
        isExpanded
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByRole('switch')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Revert/ })).toBeInTheDocument();
  });

  it('disables Revert when not user-edited', () => {
    render(
      <ProcessOptionRow
        option={layerHeight}
        value="0.16"
        revertTo="0.20"
        isUserEdited={false}
        isFileModified
        showTooltipCaption={false}
        isExpanded
        onToggleExpand={() => {}}
        onCommit={() => {}}
        onRevert={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: /Revert/ })).toBeDisabled();
  });
});
