import { Download, Heart, Info, Save, Trash2, X } from '../../luminaIcons';
import { useEffect, useMemo, useRef, useState } from 'react';
import Lightbox from 'yet-another-react-lightbox';
import Captions from 'yet-another-react-lightbox/plugins/captions';
import Counter from 'yet-another-react-lightbox/plugins/counter';
import Fullscreen from 'yet-another-react-lightbox/plugins/fullscreen';
import Slideshow from 'yet-another-react-lightbox/plugins/slideshow';
import Zoom from 'yet-another-react-lightbox/plugins/zoom';
import 'yet-another-react-lightbox/styles.css';
import 'yet-another-react-lightbox/plugins/captions.css';
import 'yet-another-react-lightbox/plugins/counter.css';

import { resolveArtworkUrl } from '../../Artwork';
import { Button, ButtonLink, Checkbox, Dialog, IconButton, Input, Select } from '../../ui';
import {
  addPhotoToAlbum, assignPhotoFace, createPhotoPerson, listPhotoAlbums, listPhotoFaces, listPhotoPeople, removePhotoFromAlbum, splitPhotoPerson, updatePhoto,
  type Photo, type PhotoAlbum, type PhotoFace, type PhotoPerson, type PhotoVisibility,
} from './photoApi';

type PhotoViewerProps = {
  photos: Photo[];
  photoId: string;
  onClose: () => void;
  onNearEnd: () => void;
  onPatch: (photo: Photo) => void;
};

const detail = (photo: Photo) => [
  photo.captured_at ? new Intl.DateTimeFormat(undefined, { dateStyle: 'long', timeStyle: 'short' }).format(new Date(photo.captured_at)) : null,
  photo.camera,
  photo.width > 0 && photo.height > 0 ? `${photo.width.toLocaleString()} × ${photo.height.toLocaleString()}` : null,
  photo.bytes > 0 ? `${(photo.bytes / 1024 / 1024).toFixed(1)} MB` : null,
].filter(Boolean).join(' · ');

const photoUrl = (url: string | null): string | null => resolveArtworkUrl(url);

export default function PhotoViewer({ photos, photoId, onClose, onNearEnd, onPatch }: PhotoViewerProps) {
  const initial = Math.max(0, photos.findIndex((photo) => photo.id === photoId));
  const [index, setIndex] = useState(initial);
  const [details, setDetails] = useState(false);
  const [albums, setAlbums] = useState<PhotoAlbum[] | null>(null);
  const [faces, setFaces] = useState<PhotoFace[] | null>(null);
  const [people, setPeople] = useState<PhotoPerson[] | null>(null);
  const [namingFace, setNamingFace] = useState<PhotoFace | null>(null);
  const [personName, setPersonName] = useState('');
  const [title, setTitle] = useState('');
  const [visibility, setVisibility] = useState<PhotoVisibility>('private');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const albumsRequested = useRef(false);
  const faceRequest = useRef<string | null>(null);
  useEffect(() => { setIndex(Math.max(0, photos.findIndex((photo) => photo.id === photoId))); }, [photoId]);
  const current = photos[index];
  useEffect(() => { setTitle(current?.title ?? ''); setVisibility(current?.visibility ?? 'private'); setError(null); setFaces(null); setPeople(null); faceRequest.current = null; }, [current?.id]);
  useEffect(() => {
    if (!details || albums !== null || albumsRequested.current) return;
    albumsRequested.current = true;
    void listPhotoAlbums().then((page) => setAlbums(page.items), (failure: unknown) => { setAlbums([]); setError(failure instanceof Error ? failure.message : 'Albums are unavailable.'); });
  }, [details, albums]);
  useEffect(() => {
    if (!details || !current?.can_edit || faceRequest.current === current.id) return undefined;
    const controller = new AbortController();
    faceRequest.current = current.id;
    const allPeople = async () => {
      const items: PhotoPerson[] = [];
      let offset: number | null = 0;
      while (offset !== null) {
        const page = await listPhotoPeople(true, controller.signal, offset, 200);
        items.push(...page.items);
        offset = page.next_offset ?? null;
      }
      return items;
    };
    void Promise.all([listPhotoFaces(current.id, controller.signal), allPeople()]).then(([facePage, peoplePage]) => {
      if (controller.signal.aborted) return;
      setFaces(facePage.items); setPeople(peoplePage);
    }, (failure: unknown) => {
      if (controller.signal.aborted) return;
      setFaces([]); setPeople([]); setError(failure instanceof Error ? failure.message : 'Face suggestions are unavailable.');
    });
    return () => { controller.abort(); faceRequest.current = null; };
  }, [current?.can_edit, current?.id, details]);
  const originalUrl = photoUrl(current?.original_url ?? null);
  const slides = useMemo(() => photos.map((photo) => ({
    src: photoUrl(photo.preview_url) ?? '',
    alt: photo.title || photo.filename,
    width: photo.width,
    height: photo.height,
    title: photo.title || photo.filename,
    description: detail(photo),
    srcSet: [
      { src: photoUrl(photo.thumbnail_url) ?? '', width: Math.min(photo.width, 400), height: Math.round(Math.min(photo.width, 400) / Math.max(0.2, photo.width / photo.height)) },
      { src: photoUrl(photo.preview_url) ?? '', width: Math.min(photo.width, 1200), height: Math.round(Math.min(photo.width, 1200) / Math.max(0.2, photo.width / photo.height)) },
      ...(photo.width > 1200 ? [{ src: photoUrl(`/api/photos/${encodeURIComponent(photo.id)}/image?width=2400`) ?? '', width: Math.min(photo.width, 2400), height: Math.round(Math.min(photo.width, 2400) / Math.max(0.2, photo.width / photo.height)) }] : []),
    ].filter((candidate, candidateIndex, all) => candidate.width > 0 && all.findIndex((value) => value.width === candidate.width) === candidateIndex),
  })), [photos]);
  const save = async () => {
    if (!current?.can_edit) return;
    setSaving(true); setError(null);
    try { onPatch(await updatePhoto(current.id, { title: title.trim() || null, visibility })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not save this photo.'); }
    finally { setSaving(false); }
  };
  const favorite = async () => {
    if (!current) return;
    setSaving(true); setError(null);
    try { onPatch(await updatePhoto(current.id, { favorite: !current.favorite })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not update this favorite.'); }
    finally { setSaving(false); }
  };
  const album = async (target: PhotoAlbum, checked: boolean) => {
    if (!current) return;
    setSaving(true); setError(null);
    try {
      if (checked) await addPhotoToAlbum(target.id, current.id); else await removePhotoFromAlbum(target.id, current.id);
      onPatch({ ...current, album_ids: checked ? [...new Set([...current.album_ids, target.id])] : current.album_ids.filter((id) => id !== target.id) });
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not update that album.'); }
    finally { setSaving(false); }
  };
  const assignFace = async (face: PhotoFace, personId: string | null) => {
    setSaving(true); setError(null);
    try {
      await assignPhotoFace(face.id, personId);
      setFaces((items) => items?.map((item) => item.id === face.id ? { ...item, person_id: personId } : item) ?? null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not correct that face.'); }
    finally { setSaving(false); }
  };
  const splitFace = async (face: PhotoFace) => {
    if (!face.person_id) return;
    setSaving(true); setError(null);
    try {
      const { person } = await splitPhotoPerson(face.person_id, [face.id]);
      setPeople((items) => items ? [...items, person] : [person]);
      setFaces((items) => items?.map((item) => item.id === face.id ? { ...item, person_id: person.id } : item) ?? null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not separate that face.'); }
    finally { setSaving(false); }
  };
  const nameFace = async () => {
    if (!current || !namingFace || !personName.trim()) return;
    setSaving(true); setError(null);
    try {
      const { person } = await createPhotoPerson(personName.trim(), [namingFace.id]);
      setPeople((items) => items && !items.some((item) => item.id === person.id) ? [...items, person] : items ?? [person]);
      const nextFaces = (faces ?? []).map((face) => face.id === namingFace.id ? { ...face, person_id: person.id } : face);
      setFaces(nextFaces);
      onPatch({ ...current, person_ids: [...new Set(nextFaces.flatMap((face) => face.person_id ? [face.person_id] : []))] });
      setNamingFace(null); setPersonName(''); setDetails(false);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not name that person.'); }
    finally { setSaving(false); }
  };
  if (namingFace) return <Dialog busy={saving} footer={<><Button onClick={() => setNamingFace(null)} variant="quiet">Cancel</Button><Button disabled={!personName.trim()} onClick={() => void nameFace()} variant="primary">Save person</Button></>} onClose={() => setNamingFace(null)} open size="sm" title="Name this person"><label className="ph-field">Name<Input autoFocus maxLength={120} onChange={(event) => setPersonName(event.target.value)} value={personName} /></label>{error ? <p className="ph-viewer-error" role="alert">{error}</p> : null}</Dialog>;
  return (
    <Lightbox
      animation={{ fade: 180, swipe: 360 }}
      captions={{ descriptionTextAlign: 'start', showToggle: false }}
      counter={{ container: { style: { top: 52 } } }}
      carousel={{ finite: true, preload: 2 }}
      close={onClose}
      controller={{ closeOnBackdropClick: true, closeOnPullDown: true }}
      index={index}
      labels={{ Lightbox: 'Photo viewer', 'Photo gallery': 'Photos' }}
      on={{ view: ({ index: next }) => { setIndex(next); if (next >= photos.length - 8) onNearEnd(); } }}
      open
      plugins={[Captions, Counter, Fullscreen, Slideshow, Zoom]}
      toolbar={{ buttons: [<IconButton icon={<Info />} key="details" label={details ? 'Hide photo details' : 'Show photo details'} onClick={() => setDetails((value) => !value)} pressed={details} variant="overlay" />, 'close'] }}
      render={{ controls: () => current ? <>
        {details ? <aside aria-label="Photo details" className="ph-viewer-panel yarl__interactive">
          <div className="ph-viewer-panel-head"><strong>Photo details</strong><IconButton icon={<X />} label="Close photo details" onClick={() => setDetails(false)} variant="plain" /></div>
          <dl><div><dt>Filename</dt><dd>{current.filename}</dd></div>{current.camera ? <div><dt>Camera</dt><dd>{current.camera}</dd></div> : null}<div><dt>Size</dt><dd>{current.width.toLocaleString()} × {current.height.toLocaleString()}</dd></div></dl>
          <Button busy={saving} icon={<Heart fill={current.favorite ? 'currentColor' : 'none'} />} onClick={() => void favorite()} pressed={current.favorite} variant="secondary">{current.favorite ? 'Favorited' : 'Add favorite'}</Button>
          {current.can_edit ? <><label>Title<Input maxLength={500} onChange={(event) => setTitle(event.target.value)} value={title} /></label><label>Who can see this<Select aria-label="Photo visibility" onChange={(event) => setVisibility(event.target.value as PhotoVisibility)} value={visibility}><option value="private">Only me</option><option value="shared">Household</option></Select></label><Button busy={saving} icon={<Save />} onClick={() => void save()} variant="primary">Save details</Button></> : null}
          {albums?.length ? <fieldset><legend>Albums</legend>{albums.filter((item) => item.can_edit).map((item) => <Checkbox checked={current.album_ids.includes(item.id)} disabled={saving} key={item.id} label={item.name} onChange={(event) => void album(item, event.target.checked)} />)}</fieldset> : null}
          {faces?.length ? <fieldset className="ph-face-corrections"><legend>People in this photo</legend><p>These are suggestions. Correct any face that is in the wrong group.</p>{faces.map((face, faceIndex) => <div className="ph-face-correction" key={face.id}>
            {photoUrl(face.thumbnail_url) ? <img alt="" src={photoUrl(face.thumbnail_url) ?? undefined} /> : <span aria-hidden="true" />}
            <label>Face {faceIndex + 1}<Select aria-label={`Person for face ${faceIndex + 1}`} disabled={saving} onChange={(event) => void assignFace(face, event.target.value || null)} value={face.person_id ?? ''}><option value="">Not grouped</option>{face.person_id && !people?.some((person) => person.id === face.person_id) ? <option value={face.person_id}>Current group</option> : null}{people?.map((person) => <option key={person.id} value={person.id}>{person.name || `Unnamed group · ${person.photo_count} photos`}</option>)}</Select></label>
            <div className="ph-face-actions"><Button disabled={saving} onClick={() => { setNamingFace(face); setPersonName(''); }} variant="quiet">Name person</Button>{face.person_id ? <Button disabled={saving} onClick={() => void splitFace(face)} variant="quiet">Separate into new group</Button> : null}</div>
          </div>)}</fieldset> : null}
          {originalUrl ? <ButtonLink download={current.filename} href={originalUrl} icon={<Download />} variant="secondary">Download original</ButtonLink> : null}
          {current.can_edit ? <Button icon={<Trash2 />} onClick={() => { setSaving(true); void updatePhoto(current.id, { deleted: true }).then((photo) => { onPatch(photo); onClose(); }, (failure: unknown) => { setError(failure instanceof Error ? failure.message : 'Lumina could not move this photo to Trash.'); setSaving(false); }); }} variant="danger">Move to Trash</Button> : null}
          {error ? <p className="ph-viewer-error" role="alert">{error}</p> : null}
        </aside> : null}
      </> : null }}
      slides={slides}
      slideshow={{ delay: 4500 }}
      zoom={{ maxZoomPixelRatio: 3, scrollToZoom: true }}
    />
  );
}
