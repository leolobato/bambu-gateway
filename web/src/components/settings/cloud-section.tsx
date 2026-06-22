import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cloud, ExternalLink, LogOut } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { getCapabilities } from '@/lib/api/capabilities';
import {
  cloudLogout,
  getCloudAuthUrl,
  getCloudStatus,
  pasteCloudLogin,
} from '@/lib/api/cloud';
import { ApiError } from '@/lib/api/client';

export function CloudSection() {
  const queryClient = useQueryClient();
  const [pasteValue, setPasteValue] = useState('');

  const capsQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: getCapabilities,
    staleTime: 60_000,
  });
  const cloudEnabled = capsQuery.data?.cloud === true;

  const statusQuery = useQuery({
    queryKey: ['cloud-status'],
    queryFn: getCloudStatus,
    enabled: cloudEnabled,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['cloud-status'] });
    queryClient.invalidateQueries({ queryKey: ['printers'] });
  };

  const openSignIn = useMutation({
    mutationFn: getCloudAuthUrl,
    onSuccess: ({ url }) => window.open(url, '_blank', 'noopener,noreferrer'),
    onError: (e) => toast.error(`Couldn't open sign-in: ${describe(e)}`),
  });

  const paste = useMutation({
    mutationFn: () => pasteCloudLogin(pasteValue.trim()),
    onSuccess: ({ profile }) => {
      toast.success(`Signed in as ${profile.name || profile.account || 'your account'}`);
      setPasteValue('');
      invalidate();
    },
    onError: (e) => toast.error(`Sign-in failed: ${describe(e)}`),
  });

  const signOut = useMutation({
    mutationFn: cloudLogout,
    onSuccess: () => {
      toast.success('Signed out of Bambu Cloud');
      invalidate();
    },
    onError: (e) => toast.error(`Sign-out failed: ${describe(e)}`),
  });

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-white px-1">Cloud Account</h2>
      <Card className="bg-card border-border p-4 flex flex-col gap-3">
        {capsQuery.isLoading ? (
          <Skeleton className="h-5 w-2/3" />
        ) : !cloudEnabled ? (
          <p className="text-sm text-text-1">
            Cloud mode is off. Set{' '}
            <code className="text-text-0">BAMBU_CLOUD_ENABLED=true</code> in the
            gateway environment to connect through your Bambu account — see{' '}
            <a
              href="https://github.com/leolobato/bambu-gateway#bambu-cloud-mode-optional"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              the README
            </a>
            .
          </p>
        ) : statusQuery.isLoading ? (
          <Skeleton className="h-14 rounded-2xl" />
        ) : statusQuery.data?.signed_in ? (
          <SignedIn
            name={statusQuery.data.profile?.name || statusQuery.data.profile?.account || 'Signed in to Bambu Cloud'}
            account={statusQuery.data.profile?.account ?? ''}
            avatar={statusQuery.data.profile?.avatar ?? ''}
            region={statusQuery.data.region}
            onSignOut={() => signOut.mutate()}
            signingOut={signOut.isPending}
          />
        ) : (
          <SignIn
            value={pasteValue}
            onChange={setPasteValue}
            onOpen={() => openSignIn.mutate()}
            opening={openSignIn.isPending}
            onPaste={() => paste.mutate()}
            pasting={paste.isPending}
          />
        )}
      </Card>
    </section>
  );
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'unexpected error';
}

function SignedIn(props: {
  name: string;
  account: string;
  avatar: string;
  region: string;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      {props.avatar ? (
        <img src={props.avatar} alt="" className="h-10 w-10 rounded-full object-cover" />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-1">
          <Cloud className="h-5 w-5 text-accent" aria-hidden />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text-0">{props.name}</p>
        <p className="truncate text-xs text-text-2">
          {props.account ? `${props.account} · ` : ''}{props.region} region
        </p>
      </div>
      <Button
        type="button"
        onClick={props.onSignOut}
        disabled={props.signingOut}
        className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-text-1 border-0 text-[13px] font-semibold"
      >
        <LogOut className="w-3.5 h-3.5 mr-1" aria-hidden /> Sign out
      </Button>
    </div>
  );
}

function SignIn(props: {
  value: string;
  onChange: (v: string) => void;
  onOpen: () => void;
  opening: boolean;
  onPaste: () => void;
  pasting: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-text-1">
        Sign in with your Bambu account to control cloud-connected printers.
      </p>
      <ol className="flex flex-col gap-2 text-sm text-text-1">
        <li className="flex items-center gap-2">
          <span className="text-text-2">1.</span>
          <Button
            type="button"
            onClick={props.onOpen}
            disabled={props.opening}
            className="rounded-full h-8 px-3 bg-surface-1 hover:bg-surface-2 text-accent border-0 text-[13px] font-semibold"
          >
            <ExternalLink className="w-3.5 h-3.5 mr-1" aria-hidden /> Open Bambu sign-in
          </Button>
        </li>
        <li className="text-text-2 pl-5 text-xs">
          After signing in, your browser will show a "this site can't be reached"
          page at <code className="text-text-1">localhost:13618</code> — that's
          expected. Copy the full URL from the address bar and paste it below.
        </li>
        <li className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={props.value}
            onChange={(e) => props.onChange(e.target.value)}
            placeholder="http://localhost:13618/?ticket=…"
            className="flex-1"
          />
          <Button
            type="button"
            onClick={props.onPaste}
            disabled={props.pasting || props.value.trim() === ''}
            className="rounded-full h-9 px-4 bg-accent hover:bg-accent/90 text-black border-0 text-[13px] font-semibold"
          >
            Complete sign-in
          </Button>
        </li>
      </ol>
    </div>
  );
}
