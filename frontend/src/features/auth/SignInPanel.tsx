import { type FormEvent, useState } from 'react';
import { Button, Checkbox, Field, fieldProps, Input, PasswordInput } from '../../ui';

export interface SignInPanelProps {
  busy: boolean;
  error: string | null;
  onSubmit: (username: string, password: string, rememberOnDevice: boolean) => void;
  /** Present when the ring has members: returns to "Who's watching?". */
  onChoosePicker?: () => void;
}

/** The one sign-in form: the sign-in page and the signed-in picker's "Someone else" both render it (app polish 6.1, 6.2). */
export function SignInPanel({ busy, error, onSubmit, onChoosePicker }: SignInPanelProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy) onSubmit(username, password, remember);
  }
  return (
    <form className="g-auth-form" noValidate onSubmit={submit}>
      <Field label="Username">{(ids) => <Input {...fieldProps(ids)} autoComplete="username" onChange={(event) => setUsername(event.target.value)} value={username} />}</Field>
      <Field error={error} label="Password">
        {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} value={password} />}
      </Field>
      <Checkbox checked={remember} label="Show me in Who's watching on this device" onChange={(event) => setRemember(event.target.checked)} />
      <Button busy={busy} disabled={!username.trim() || !password} type="submit" variant="primary" wide>Sign in</Button>
      {onChoosePicker ? <Button onClick={onChoosePicker} variant="quiet" wide>Choose who's watching</Button> : null}
    </form>
  );
}
