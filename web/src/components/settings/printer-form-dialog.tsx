import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  createPrinterConfig,
  updatePrinterConfig,
} from '@/lib/api/printer-configs';
import { getSlicerMachines } from '@/lib/api/slicer-profiles';
import type { PrinterConfigInput, PrinterConfigResponse } from '@/lib/api/types';

export type PrinterFormMode =
  | { kind: 'add' }
  | { kind: 'edit'; printer: PrinterConfigResponse };

interface FormState {
  name: string;
  serial: string;
  ip: string;
  access_code: string;
  machine_model: string;
}

interface FieldErrors {
  serial?: string;
  ip?: string;
  access_code?: string;
}

function validate(state: FormState, isEdit: boolean): FieldErrors {
  const errors: FieldErrors = {};
  if (!state.serial.trim()) errors.serial = 'Required';
  if (!state.ip.trim()) errors.ip = 'Required';
  // Access code is required only when adding; empty on edit means "keep existing".
  if (!isEdit && !state.access_code.trim()) errors.access_code = 'Required';
  return errors;
}

export function PrinterFormDialog({
  mode,
  open,
  onClose,
}: {
  mode: PrinterFormMode | null;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const isEdit = mode?.kind === 'edit';

  const [state, setState] = useState<FormState>({
    name: '',
    serial: '',
    ip: '',
    access_code: '',
    machine_model: '',
  });
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  // Reset form whenever the mode changes (open/close cycle).
  useEffect(() => {
    if (mode?.kind === 'edit') {
      setState({
        name: mode.printer.name,
        serial: mode.printer.serial,
        ip: mode.printer.ip,
        access_code: '',
        machine_model: mode.printer.machine_model,
      });
    } else if (mode?.kind === 'add') {
      setState({ name: '', serial: '', ip: '', access_code: '', machine_model: '' });
    }
    setTouched({});
  }, [mode]);

  const machinesQuery = useQuery({
    queryKey: ['slicer', 'machines'],
    queryFn: getSlicerMachines,
    staleTime: Infinity,
    enabled: open,
  });

  const machineOptions = useMemo(
    () =>
      (machinesQuery.data ?? [])
        .filter((m) => m.setting_id)
        .map((m) => ({ value: m.setting_id, label: m.name })),
    [machinesQuery.data],
  );

  const errors = validate(state, isEdit);
  const hasErrors = Object.keys(errors).length > 0;

  const submit = useMutation({
    mutationFn: async () => {
      const input: PrinterConfigInput = {
        serial: state.serial.trim(),
        ip: state.ip.trim(),
        access_code: state.access_code.trim(),
        name: state.name.trim(),
        machine_model: state.machine_model,
      };
      if (mode?.kind === 'edit') {
        return updatePrinterConfig(mode.printer.serial, input);
      }
      return createPrinterConfig(input);
    },
    onSuccess: () => {
      toast.success(isEdit ? 'Printer updated' : 'Printer added');
      queryClient.invalidateQueries({ queryKey: ['printer-configs'] });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      onClose();
    },
    onError: (err: Error) => {
      toast.error(`${isEdit ? 'Update' : 'Add'} failed: ${err.message}`);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched({ serial: true, ip: true, access_code: true });
    if (hasErrors) return;
    submit.mutate();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="bg-bg-1 border-border text-text-0 max-w-md">
        <DialogHeader>
          <DialogTitle className="text-white">
            {isEdit ? 'Edit Printer' : 'Add Printer'}
          </DialogTitle>
          <DialogDescription className="text-text-1">
            {isEdit
              ? 'Update connection details. Leave Access Code blank to keep the current value.'
              : 'Enter the printer connection details from Bambu Studio or the printer LCD.'}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <Field
            label="Name"
            value={state.name}
            onChange={(name) => setState((s) => ({ ...s, name }))}
            placeholder="Living Room A1 Mini"
          />
          <Field
            label="Serial"
            value={state.serial}
            onChange={(serial) => setState((s) => ({ ...s, serial }))}
            error={touched.serial ? errors.serial : undefined}
            disabled={isEdit}
            required
          />
          <Field
            label="IP Address"
            value={state.ip}
            onChange={(ip) => setState((s) => ({ ...s, ip }))}
            error={touched.ip ? errors.ip : undefined}
            placeholder="192.168.1.42"
            required
          />
          <Field
            label="Access Code"
            value={state.access_code}
            onChange={(access_code) => setState((s) => ({ ...s, access_code }))}
            error={touched.access_code ? errors.access_code : undefined}
            type="password"
            placeholder={isEdit ? 'Leave blank to keep current' : '8 digits'}
            required={!isEdit}
          />
          <div className="flex flex-col gap-1">
            <Label htmlFor="machine_model" className="text-xs text-text-1">
              Machine Model
            </Label>
            <Select
              value={state.machine_model || '__none__'}
              onValueChange={(v) =>
                setState((s) => ({ ...s, machine_model: v === '__none__' ? '' : v }))
              }
            >
              <SelectTrigger id="machine_model" className="bg-bg-0 border-border">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">None (no filament filtering)</SelectItem>
                {machineOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <DialogFooter className="mt-2 gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={submit.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={submit.isPending || hasErrors}
              className="bg-gradient-to-r from-accent-strong to-accent text-white border-0"
            >
              {submit.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Add Printer'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  type = 'text',
  placeholder,
  disabled = false,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  error?: string;
  type?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
}) {
  const id = `field-${label.toLowerCase().replace(/\s+/g, '-')}`;
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-text-1">
        {label}
        {required && <span className="text-danger ml-0.5">*</span>}
      </Label>
      <Input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="bg-bg-0 border-border text-text-0"
      />
      {error && <span className="text-[11px] text-danger">{error}</span>}
    </div>
  );
}
